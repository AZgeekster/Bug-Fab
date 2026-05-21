defmodule BugFab.IntakeRouterTest do
  use ExUnit.Case, async: false
  use Plug.Test

  alias BugFab.IntakeRouter
  alias BugFab.Test.Fixtures

  @opts IntakeRouter.init([])

  setup do
    dir = Fixtures.tmp_storage_dir()
    Application.put_env(:bug_fab, :storage, {BugFab.Storage.FileStorage, [storage_dir: dir]})
    Application.put_env(:bug_fab, :rate_limit_enabled, false)
    Application.put_env(:bug_fab, :max_upload_mb, 4)
    BugFab.reset_storage_handle()
    BugFab.RateLimiter.reset()

    on_exit(fn ->
      BugFab.reset_storage_handle()
      File.rm_rf!(dir)
    end)

    :ok
  end

  defp submit(metadata, screenshot_bytes, extra_headers \\ []) do
    boundary = "abcboundary"
    body = Fixtures.multipart_body(metadata, screenshot_bytes, boundary)

    base =
      conn(:post, "/bug-reports", body)
      |> put_req_header("content-type", Fixtures.content_type(boundary))
      |> put_req_header("user-agent", "test-agent/1.0")

    extra_headers
    |> Enum.reduce(base, fn {k, v}, acc -> put_req_header(acc, k, v) end)
    |> IntakeRouter.call(@opts)
  end

  test "201 on valid submission, returns intake envelope" do
    conn = submit(Fixtures.metadata(), Fixtures.tiny_png())
    assert conn.status == 201
    body = Jason.decode!(conn.resp_body)
    assert body["id"] =~ ~r/^bug-\d{3,}$/
    assert is_binary(body["received_at"])
    assert String.starts_with?(body["stored_at"], "bug-fab://reports/")
  end

  test "422 on unknown severity (schema_error)" do
    conn = submit(Fixtures.metadata(%{"severity" => "urgent"}), Fixtures.tiny_png())
    assert conn.status == 422
    body = Jason.decode!(conn.resp_body)
    assert body["error"] == "schema_error"
  end

  test "400 on missing protocol_version (unsupported_protocol_version)" do
    bad = Map.delete(Fixtures.metadata(), "protocol_version")
    conn = submit(bad, Fixtures.tiny_png())
    assert conn.status == 400
    assert Jason.decode!(conn.resp_body)["error"] == "unsupported_protocol_version"
  end

  test "415 on non-PNG screenshot (magic bytes fail)" do
    conn = submit(Fixtures.metadata(), "JFIF-but-fake")
    assert conn.status == 415
    assert Jason.decode!(conn.resp_body)["error"] == "unsupported_media_type"
  end

  test "413 when screenshot exceeds the configured cap" do
    Application.put_env(:bug_fab, :max_upload_mb, 1)
    BugFab.reset_storage_handle()
    # Build a 'PNG' (correct magic) larger than 1 MiB.
    payload = <<0x89, "PNG", 0x0D, 0x0A, 0x1A, 0x0A>> <> :crypto.strong_rand_bytes(2 * 1024 * 1024)
    conn = submit(Fixtures.metadata(), payload)
    assert conn.status == 413
    body = Jason.decode!(conn.resp_body)
    assert body["error"] == "payload_too_large"
    assert body["limit_bytes"] == 1 * 1024 * 1024
    Application.put_env(:bug_fab, :max_upload_mb, 4)
  end

  test "429 when rate limit exceeded" do
    Application.put_env(:bug_fab, :rate_limit_enabled, true)
    Application.put_env(:bug_fab, :rate_limit_max, 1)
    Application.put_env(:bug_fab, :rate_limit_window_seconds, 60)
    BugFab.RateLimiter.reset()

    assert submit(Fixtures.metadata(), Fixtures.tiny_png()).status == 201
    conn = submit(Fixtures.metadata(), Fixtures.tiny_png())
    assert conn.status == 429
    body = Jason.decode!(conn.resp_body)
    assert body["error"] == "rate_limited"
    assert is_integer(body["retry_after_seconds"])

    Application.put_env(:bug_fab, :rate_limit_enabled, false)
  end

  test "server_user_agent is captured from request header, not client value" do
    meta =
      Fixtures.metadata(%{
        "context" => %{
          "user_agent" => "ClientSuppliedUA/9.9",
          "environment" => "prod"
        }
      })

    conn = submit(meta, Fixtures.tiny_png())
    assert conn.status == 201
    id = Jason.decode!(conn.resp_body)["id"]

    {mod, state} = BugFab.storage_handle()
    {:ok, stored} = mod.get_report(state, id)
    assert stored["server_user_agent"] == "test-agent/1.0"
    assert stored["client_reported_user_agent"] == "ClientSuppliedUA/9.9"
  end
end
