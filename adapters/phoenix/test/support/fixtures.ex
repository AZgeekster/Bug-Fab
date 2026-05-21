defmodule BugFab.Test.Fixtures do
  @moduledoc false

  @png_signature <<0x89, "PNG", 0x0D, 0x0A, 0x1A, 0x0A>>
  # 1x1 transparent PNG body following the magic signature; enough to look
  # like a real image to anyone who checks magic bytes (which is what the
  # adapter does — it does not parse IHDR).
  @png_body <<0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1, 8, 6, 0, 0, 0, 31, 21, 196, 137>>

  def tiny_png, do: @png_signature <> @png_body

  def metadata(overrides \\ %{}) do
    %{
      "protocol_version" => "0.1",
      "title" => "Save button is unresponsive",
      "client_ts" => "2026-04-27T15:29:58-07:00",
      "report_type" => "bug",
      "description" => "Click does nothing on the cart page.",
      "expected_behavior" => "Cart should save and proceed.",
      "severity" => "high",
      "tags" => ["regression", "checkout"],
      "reporter" => %{"email" => "alice@example.com"},
      "context" => %{
        "url" => "https://example.com/cart",
        "module" => "checkout",
        "user_agent" => "Mozilla/5.0 (test)",
        "viewport_width" => 1920,
        "viewport_height" => 1080,
        "app_version" => "1.4.2",
        "environment" => "prod"
      }
    }
    |> Map.merge(overrides)
  end

  @doc "Build a raw multipart body suitable for Plug.Test.conn/3."
  def multipart_body(metadata_map, screenshot_bytes, boundary \\ "abcboundary") do
    metadata_json = Jason.encode!(metadata_map)

    [
      "--", boundary, "\r\n",
      "Content-Disposition: form-data; name=\"metadata\"\r\n",
      "Content-Type: application/json\r\n\r\n",
      metadata_json, "\r\n",
      "--", boundary, "\r\n",
      "Content-Disposition: form-data; name=\"screenshot\"; filename=\"shot.png\"\r\n",
      "Content-Type: image/png\r\n\r\n",
      screenshot_bytes, "\r\n",
      "--", boundary, "--\r\n"
    ]
    |> IO.iodata_to_binary()
  end

  def content_type(boundary), do: "multipart/form-data; boundary=#{boundary}"

  def tmp_storage_dir do
    dir = Path.join([System.tmp_dir!(), "bug_fab_test", random_suffix()])
    File.mkdir_p!(dir)
    dir
  end

  defp random_suffix do
    :crypto.strong_rand_bytes(8) |> Base.url_encode64(padding: false)
  end

  @doc "Reset the rate limiter and clear stored storage configuration for the test."
  def with_storage(fun) do
    dir = tmp_storage_dir()
    BugFab.RateLimiter.reset()
    Application.put_env(:bug_fab, :storage, {BugFab.Storage.FileStorage, [storage_dir: dir]})
    Application.put_env(:bug_fab, :rate_limit_enabled, false)
    Application.put_env(:bug_fab, :max_upload_mb, 4)
    BugFab.reset_storage_handle()

    try do
      fun.(dir)
    after
      BugFab.reset_storage_handle()
      File.rm_rf!(dir)
    end
  end
end
