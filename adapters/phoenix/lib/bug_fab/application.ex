defmodule BugFab.Application do
  @moduledoc """
  Top-level supervisor for the Bug-Fab adapter.

  Children are intentionally minimal so that Bug-Fab can be embedded in a
  host Phoenix application without dictating supervision-tree shape:

  * `BugFab.RateLimiter` — an ETS-backed per-IP limiter. Always started so
    that the ETS table exists; consumers who disable rate-limiting just
    bypass the check at request time.

  Consumers who want the Ecto backend supervise their own Repo elsewhere
  (typically the host application's supervision tree). This module does
  NOT start a Repo — see `MIGRATION_NOTES.md` § "Ecto Repo lifecycle".
  """

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      BugFab.RateLimiter
    ]

    opts = [strategy: :one_for_one, name: BugFab.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
