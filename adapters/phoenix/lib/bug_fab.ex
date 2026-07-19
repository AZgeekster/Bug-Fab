defmodule BugFab do
  @moduledoc """
  Bug-Fab Phoenix / Plug adapter — entry point + configuration accessors.

  Bug-Fab is a wire-protocol-first bug-reporting tool. This package ships
  two mountable Plug routers (`BugFab.IntakeRouter`, `BugFab.ViewerRouter`)
  that implement the v0.1 wire protocol on top of either:

  * `BugFab.Storage.FileStorage` — JSON-on-disk (zero external deps).
  * `BugFab.Storage.EctoStorage` — Postgres or SQLite via Ecto.

  ## Mounting

      # In a Phoenix endpoint:
      forward "/api", BugFab.IntakeRouter
      forward "/admin/bug-reports", BugFab.ViewerRouter

      # Or as a standalone Plug.Cowboy app:
      Plug.Cowboy.child_spec(scheme: :http, plug: BugFab.IntakeRouter, options: [port: 4000])

  ## Configuration

      config :bug_fab,
        storage: {BugFab.Storage.FileStorage, [storage_dir: "/var/bug-fab"]},
        max_upload_mb: 4,
        rate_limit_enabled: false,
        rate_limit_max: 30,
        rate_limit_window_seconds: 60,
        viewer_permissions: %{can_edit_status: true, can_delete: true, can_bulk: true},
        actor_resolver: nil,
        id_prefix: ""

  ## Wire protocol

  The wire shape is defined in `docs/PROTOCOL.md` in the upstream Bug-Fab
  repo. This adapter exposes the eight required endpoints and matches the
  Python reference adapter's response shapes byte-for-byte where the
  protocol prescribes them.

  See `MIGRATION_NOTES.md` for BEAM/OTP supervision-tree, release-mode,
  and clustering caveats.
  """

  @protocol_version "0.1"

  @doc "Return the wire-protocol version this adapter implements."
  @spec protocol_version() :: String.t()
  def protocol_version, do: @protocol_version

  @doc """
  Resolve the configured storage backend.

  The application's `:bug_fab` env is read; if a `{module, opts}` tuple is
  configured, the module's `child_spec/1` (or `init/1` for non-supervised
  backends) is used. Falls back to a `FileStorage` rooted at `tmp/bug-fab`
  for development convenience — production deployments MUST configure
  storage explicitly.
  """
  @spec storage() :: {module(), keyword()}
  def storage do
    case Application.get_env(:bug_fab, :storage) do
      {mod, opts} when is_atom(mod) and is_list(opts) -> {mod, opts}
      nil -> {BugFab.Storage.FileStorage, [storage_dir: Path.join(System.tmp_dir!(), "bug-fab")]}
      other -> raise ArgumentError, "Invalid :bug_fab :storage config: #{inspect(other)}"
    end
  end

  @doc "Resolve a single config key with a default."
  @spec config(atom(), term()) :: term()
  def config(key, default \\ nil), do: Application.get_env(:bug_fab, key, default)

  @doc """
  Convenience wrapper that materializes the configured storage backend
  into a runtime handle. Returns `{module, state}` — both intake and
  viewer routers use this to call into storage in a backend-agnostic way.

  The handle is cached in `:persistent_term` so repeat lookups are O(1)
  and the FileStorage GenServer (if any) is started exactly once per
  unique `{module, opts}` pair. Tests that reconfigure storage between
  cases should call `BugFab.reset_storage_handle/0` to invalidate the cache.
  """
  @spec storage_handle() :: {module(), term()}
  def storage_handle do
    key = {__MODULE__, :storage_handle, storage()}

    case :persistent_term.get(key, :__none__) do
      :__none__ ->
        {mod, opts} = storage()
        state = mod.handle(opts)
        :persistent_term.put(key, {mod, state})
        {mod, state}

      cached ->
        cached
    end
  end

  @doc "Drop the cached storage handle (test helper)."
  @spec reset_storage_handle() :: :ok
  def reset_storage_handle do
    # persistent_term doesn't support listing keys; we erase the current
    # config's key explicitly. Tests typically rotate the storage_dir
    # between cases, so the key naturally differs and the old entry is
    # left orphaned — acceptable for a test helper.
    key = {__MODULE__, :storage_handle, storage()}
    _ = :persistent_term.erase(key)
    :ok
  end
end
