# Minimal Bug-Fab adapter example using Plug.Cowboy.
#
# Run from the package root:
#
#     mix run --no-halt examples/minimal/minimal.exs
#
# Then submit a report:
#
#     curl -X POST http://localhost:4000/api/bug-reports \
#       -F 'metadata={"protocol_version":"0.1","title":"Test","client_ts":"2026-04-27T00:00:00Z"}' \
#       -F 'screenshot=@/path/to/shot.png;type=image/png'
#
# View at <http://localhost:4000/admin/bug-reports/>.

Application.put_env(:bug_fab, :storage,
  {BugFab.Storage.FileStorage, [storage_dir: Path.join(System.tmp_dir!(), "bug-fab-demo")]}
)

defmodule BugFab.Example.Router do
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

{:ok, _} =
  Plug.Cowboy.http(BugFab.Example.Router, [], port: 4000)

IO.puts("Bug-Fab minimal example listening on http://localhost:4000")
Process.sleep(:infinity)
