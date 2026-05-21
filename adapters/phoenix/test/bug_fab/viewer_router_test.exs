defmodule BugFab.ViewerRouterTest do
  use ExUnit.Case, async: false
  use Plug.Test

  alias BugFab.ViewerRouter
  alias BugFab.Test.Fixtures

  @opts ViewerRouter.init([])

  setup do
    dir = Fixtures.tmp_storage_dir()
    Application.put_env(:bug_fab, :storage, {BugFab.Storage.FileStorage, [storage_dir: dir]})

    Application.put_env(:bug_fab, :viewer_permissions, %{
      can_edit_status: true,
      can_delete: true,
      can_bulk: true
    })

    BugFab.reset_storage_handle()

    on_exit(fn ->
      BugFab.reset_storage_handle()
      File.rm_rf!(dir)
    end)

    # Seed two reports for read tests.
    {mod, state} = BugFab.storage_handle()
    {:ok, id1} = mod.save_report(state, seeded_meta("one", "high"), Fixtures.tiny_png())
    {:ok, id2} = mod.save_report(state, seeded_meta("two", "low"), Fixtures.tiny_png())
    {:ok, id1: id1, id2: id2}
  end

  test "GET /reports returns a paginated list with stats", %{id1: id1} do
    conn = ViewerRouter.call(conn(:get, "/reports"), @opts)
    assert conn.status == 200
    body = Jason.decode!(conn.resp_body)
    assert body["total"] == 2
    assert Map.has_key?(body["stats"], "open")
    assert Enum.any?(body["items"], &(&1["id"] == id1))
  end

  test "GET /reports?severity=high filters", %{id1: id1} do
    conn = ViewerRouter.call(conn(:get, "/reports?severity=high"), @opts)
    body = Jason.decode!(conn.resp_body)
    assert body["total"] == 1
    assert hd(body["items"])["id"] == id1
  end

  test "GET /reports/:id returns detail", %{id1: id} do
    conn = ViewerRouter.call(conn(:get, "/reports/#{id}"), @opts)
    assert conn.status == 200
    body = Jason.decode!(conn.resp_body)
    assert body["id"] == id
    assert body["server_user_agent"] == ""
    assert body["protocol_version"] == "0.1"
  end

  test "GET /reports/:id with bad id shape returns 404" do
    conn = ViewerRouter.call(conn(:get, "/reports/../etc/passwd"), @opts)
    assert conn.status == 404
  end

  test "GET /reports/:id/screenshot returns PNG bytes", %{id1: id} do
    conn = ViewerRouter.call(conn(:get, "/reports/#{id}/screenshot"), @opts)
    assert conn.status == 200
    [ct] = get_resp_header(conn, "content-type")
    assert ct =~ "image/png"
    assert byte_size(conn.resp_body) > 0
  end

  test "PUT /reports/:id/status updates status and appends lifecycle", %{id1: id} do
    body = Jason.encode!(%{"status" => "fixed", "fix_commit" => "deadbeef"})

    conn =
      conn(:put, "/reports/#{id}/status", body)
      |> put_req_header("content-type", "application/json")
      |> ViewerRouter.call(@opts)

    assert conn.status == 200
    detail = Jason.decode!(conn.resp_body)
    assert detail["status"] == "fixed"

    last = List.last(detail["lifecycle"])
    assert last["status"] == "fixed"
    assert last["fix_commit"] == "deadbeef"
  end

  test "PUT /reports/:id/status returns 422 on unknown status", %{id1: id} do
    body = Jason.encode!(%{"status" => "resolved"})

    conn =
      conn(:put, "/reports/#{id}/status", body)
      |> put_req_header("content-type", "application/json")
      |> ViewerRouter.call(@opts)

    assert conn.status == 422
    assert Jason.decode!(conn.resp_body)["error"] == "schema_error"
  end

  test "PUT /reports/:id/status returns 403 when permission disabled", %{id1: id} do
    Application.put_env(:bug_fab, :viewer_permissions, %{
      can_edit_status: false,
      can_delete: true,
      can_bulk: true
    })

    body = Jason.encode!(%{"status" => "fixed"})

    conn =
      conn(:put, "/reports/#{id}/status", body)
      |> put_req_header("content-type", "application/json")
      |> ViewerRouter.call(@opts)

    assert conn.status == 403
  end

  test "DELETE /reports/:id returns 204 then 404", %{id1: id} do
    c1 = ViewerRouter.call(conn(:delete, "/reports/#{id}"), @opts)
    assert c1.status == 204

    c2 = ViewerRouter.call(conn(:delete, "/reports/#{id}"), @opts)
    assert c2.status == 404
  end

  test "POST /bulk-close-fixed transitions fixed to closed", %{id1: id} do
    body = Jason.encode!(%{"status" => "fixed"})

    _ =
      conn(:put, "/reports/#{id}/status", body)
      |> put_req_header("content-type", "application/json")
      |> ViewerRouter.call(@opts)

    conn = ViewerRouter.call(conn(:post, "/bulk-close-fixed"), @opts)
    assert conn.status == 200
    assert Jason.decode!(conn.resp_body)["closed"] == 1
  end

  test "POST /bulk-archive-closed archives closed reports", %{id1: id} do
    body = Jason.encode!(%{"status" => "closed"})

    _ =
      conn(:put, "/reports/#{id}/status", body)
      |> put_req_header("content-type", "application/json")
      |> ViewerRouter.call(@opts)

    conn = ViewerRouter.call(conn(:post, "/bulk-archive-closed"), @opts)
    assert conn.status == 200
    assert Jason.decode!(conn.resp_body)["archived"] == 1
  end

  test "GET / returns the minimal HTML index" do
    conn = ViewerRouter.call(conn(:get, "/"), @opts)
    assert conn.status == 200
    [ct] = get_resp_header(conn, "content-type")
    assert ct =~ "text/html"
    assert conn.resp_body =~ "Bug-Fab Viewer"
  end

  defp seeded_meta(title, severity) do
    Fixtures.metadata(%{"title" => title, "severity" => severity})
    |> Map.put("server_user_agent", "")
    |> Map.put("client_reported_user_agent", "")
    |> Map.put("environment", "test")
  end
end
