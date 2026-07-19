defmodule BugFab.RateLimiter do
  @moduledoc """
  Tiny ETS-backed per-IP rate limiter.

  Sized to be replaceable with `Hammer` in any consumer who already has
  it in their tree — the interface is intentionally narrow: `check/1`
  returns either `:ok` or `{:error, retry_after}` based on the
  application-wide `:bug_fab` config.

  Algorithm: sliding fixed-window. Each IP gets a counter and a
  window-start timestamp. When the window expires, the counter resets.
  When the cap is exceeded mid-window, the function returns
  `{:error, seconds_until_reset}` so the intake router can honor the
  protocol's `retry_after_seconds` recommendation.

  Concurrency: ETS `:public, :set` with `:read_concurrency` enabled.
  Race condition between the read and the update is acceptable — the
  cap is approximate, which matches the protocol's "best-effort" wording.
  """

  use GenServer

  @table :bug_fab_rate_limit

  # ----- public API -----

  def start_link(opts \\ []) do
    GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  end

  @doc """
  Check whether `ip` is allowed to submit right now.

  Returns `:ok` to admit the request or `{:error, retry_after_seconds}`
  to reject it. When rate-limiting is disabled in config, always returns
  `:ok` without consulting ETS.
  """
  @spec check(String.t()) :: :ok | {:error, non_neg_integer()}
  def check(ip) when is_binary(ip) do
    if BugFab.config(:rate_limit_enabled, false) do
      do_check(ip, now_seconds())
    else
      :ok
    end
  end

  @doc "Test helper — wipes the limiter state."
  def reset do
    ensure_table()
    :ets.delete_all_objects(@table)
    :ok
  end

  # ----- GenServer plumbing -----

  @impl true
  def init(_opts) do
    ensure_table()
    {:ok, %{}}
  end

  defp ensure_table do
    case :ets.whereis(@table) do
      :undefined ->
        :ets.new(@table, [:set, :public, :named_table, read_concurrency: true])

      _ ->
        @table
    end
  end

  defp do_check(ip, now) do
    ensure_table()
    window = BugFab.config(:rate_limit_window_seconds, 60)
    max = BugFab.config(:rate_limit_max, 30)

    case :ets.lookup(@table, ip) do
      [] ->
        :ets.insert(@table, {ip, 1, now})
        :ok

      [{^ip, count, started}] ->
        if now - started >= window do
          :ets.insert(@table, {ip, 1, now})
          :ok
        else
          if count + 1 > max do
            retry_after = max(1, window - (now - started))
            {:error, retry_after}
          else
            :ets.insert(@table, {ip, count + 1, started})
            :ok
          end
        end
    end
  end

  defp now_seconds, do: System.system_time(:second)
end
