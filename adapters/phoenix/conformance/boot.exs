# Conformance-harness boot script for the Bug-Fab Phoenix/Plug adapter.
#
# Mirrors `examples/minimal/minimal.exs` but binds to port 8080 (the port
# the cross-stack harness expects) instead of 4000. The example file is
# treated as read-only for harness purposes — this script is the wrapper
# Andrew's CLAUDE rules require when an example doesn't expose an env-var
# override for its listen port.
#
# Run from the adapter root (with deps fetched / compiled):
#
#     elixir conformance/boot.exs
#
# Routes (matching examples/minimal/minimal.exs):
#
#     POST   /api/bug-reports                            — intake
#     GET    /admin/bug-reports/                         — viewer HTML index
#     GET    /admin/bug-reports/reports                  — JSON list
#     GET    /admin/bug-reports/reports/:id              — JSON detail
#     GET    /admin/bug-reports/reports/:id/screenshot   — PNG bytes
#     PUT    /admin/bug-reports/reports/:id/status       — status update
#     DELETE /admin/bug-reports/reports/:id              — hard delete
#     POST   /admin/bug-reports/bulk-close-fixed         — bulk close
#     POST   /admin/bug-reports/bulk-archive-closed      — bulk archive

storage_dir =
  System.get_env("BUG_FAB_STORAGE_DIR") ||
    Path.join(System.tmp_dir!(), "bug-fab-conformance")

Application.put_env(:bug_fab, :storage,
  {BugFab.Storage.FileStorage, [storage_dir: storage_dir]}
)

defmodule BugFab.Conformance.Router do
  @moduledoc false
  use Plug.Router

  plug :match
  plug :dispatch

  forward "/api", to: BugFab.IntakeRouter
  forward "/admin/bug-reports", to: BugFab.ViewerRouter

  match _ do
    send_resp(conn, 404, "Not Found")
  end
end

port =
  case System.get_env("PORT") do
    nil -> 8080
    str -> String.to_integer(str)
  end

{:ok, _} =
  Plug.Cowboy.http(BugFab.Conformance.Router, [], port: port)

IO.puts("bug-fab phoenix-adapter conformance boot listening on http://0.0.0.0:#{port}")
IO.puts("  intake: POST /api/bug-reports")
IO.puts("  viewer: /admin/bug-reports/...")
Process.sleep(:infinity)
