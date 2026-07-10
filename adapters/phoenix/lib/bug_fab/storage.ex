defmodule BugFab.Storage do
  @moduledoc """
  Storage backend behaviour for Bug-Fab adapters.

  Implementations:

  * `BugFab.Storage.FileStorage` — JSON-on-disk (zero external deps).
  * `BugFab.Storage.EctoStorage` — Postgres / SQLite via Ecto.

  Method signatures intentionally match the Python reference adapter's
  `Storage` ABC. Return values are plain maps (the wire-protocol shape)
  so router code never sees backend-specific structs.
  """

  @type report_id :: String.t()
  @type metadata :: map()
  @type screenshot :: binary()
  @type filters :: map()
  @type page :: pos_integer()
  @type page_size :: pos_integer()
  @type state :: term()

  @callback handle(opts :: keyword()) :: state()

  @callback save_report(state(), metadata(), screenshot()) ::
              {:ok, report_id()} | {:error, term()}

  @callback get_report(state(), report_id()) ::
              {:ok, map()} | {:error, :not_found}

  @callback list_reports(state(), filters(), page(), page_size()) ::
              {:ok, %{items: [map()], total: non_neg_integer()}}

  @callback list_stats(state()) :: %{String.t() => non_neg_integer()}

  @callback get_screenshot(state(), report_id()) ::
              {:ok, binary()} | {:error, :not_found}

  @callback update_status(state(), report_id(), map(), keyword()) ::
              {:ok, map()} | {:error, :not_found}

  @callback set_github_link(state(), report_id(), integer(), String.t()) ::
              {:ok, map()} | {:error, :not_found}

  @callback delete_report(state(), report_id()) :: :ok | {:error, :not_found}

  @callback bulk_close_fixed(state(), keyword()) :: {:ok, non_neg_integer()}

  @callback bulk_archive_closed(state()) :: {:ok, non_neg_integer()}
end
