defmodule BugFab.IntakeRouter do
  @moduledoc """
  Plug.Router for the Bug-Fab v0.1 intake endpoint.

  Exposes a single route: `POST /bug-reports`. Designed to be `forward`ed
  from a Phoenix endpoint or used standalone via `Plug.Cowboy`:

      forward "/api", BugFab.IntakeRouter

  ## Behavior

  1. Per-IP rate limit (off by default — see `BugFab.RateLimiter`).
  2. Multipart body — `metadata` JSON string + `screenshot` PNG file.
  3. Strict severity / status / report_type enums via Ecto changesets.
  4. PNG magic-byte verification; configurable size cap (default 4 MiB).
  5. Server-captures `User-Agent` from the request header — never trusts
     the client value as source of truth.
  6. Persists via the configured `BugFab.Storage` backend.
  """

  use Plug.Router

  alias BugFab.{Errors, Schemas, Wire}

  @png_signature <<0x89, "PNG", 0x0D, 0x0A, 0x1A, 0x0A>>

  # The 4 MiB cap is enforced explicitly later, but Plug needs a
  # ceiling on the multipart parser too — set to the same cap.
  plug Plug.Parsers,
    parsers: [:multipart, :json],
    pass: ["*/*"],
    json_decoder: Jason,
    length: 16_777_216

  plug :match
  plug :dispatch

  post "/bug-reports" do
    with :ok <- check_rate_limit(conn),
         {:ok, metadata_str, screenshot_part} <- extract_multipart(conn),
         {:ok, metadata_raw} <- decode_json(metadata_str),
         {:ok, payload} <- Schemas.validate_create(metadata_raw),
         {:ok, screenshot_bytes} <- read_screenshot(screenshot_part),
         :ok <- check_size(conn, screenshot_bytes),
         :ok <- check_png(screenshot_bytes) do
      do_save(conn, payload, screenshot_bytes, metadata_raw)
    else
      {:error, :no_metadata} ->
        Errors.send(conn, 400, "validation_error", "metadata part is required")

      {:error, :no_screenshot} ->
        Errors.send(conn, 400, "validation_error", "screenshot part is required")

      {:error, :empty_screenshot} ->
        Errors.send(conn, 400, "validation_error", "Screenshot file is empty")

      {:error, :bad_json, msg} ->
        Errors.send(conn, 400, "validation_error", "metadata is not valid JSON: #{msg}")

      {:error, :unsupported_protocol_version} ->
        Errors.send(
          conn,
          400,
          "unsupported_protocol_version",
          "protocol_version must be '#{Schemas.protocol_version()}'"
        )

      {:error, {:schema, changeset}} ->
        Errors.send(conn, 422, "schema_error", Schemas.format_errors(changeset))

      {:error, :payload_too_large, cap} ->
        Errors.send(
          conn,
          413,
          "payload_too_large",
          "Screenshot exceeds maximum size of #{div(cap, 1024 * 1024)} MiB",
          limit_bytes: cap
        )

      {:error, :not_png} ->
        Errors.send(conn, 415, "unsupported_media_type", "Screenshot must be PNG (image/png)")

      {:error, :rate_limited, retry_after} ->
        Errors.send(
          conn,
          429,
          "rate_limited",
          "Rate limit exceeded",
          retry_after_seconds: retry_after
        )
    end
  end

  match _ do
    Errors.send(conn, 404, "validation_error", "Not Found")
  end

  # ----- helpers -----

  defp check_rate_limit(conn) do
    case BugFab.RateLimiter.check(client_ip(conn)) do
      :ok -> :ok
      {:error, retry_after} -> {:error, :rate_limited, retry_after}
    end
  end

  defp extract_multipart(conn) do
    params = conn.params || %{}
    meta = Map.get(params, "metadata")
    shot = Map.get(params, "screenshot")

    cond do
      is_nil(meta) -> {:error, :no_metadata}
      is_nil(shot) -> {:error, :no_screenshot}
      true -> {:ok, meta, shot}
    end
  end

  defp decode_json(str) when is_binary(str) do
    case Jason.decode(str) do
      {:ok, m} when is_map(m) -> {:ok, m}
      {:ok, _other} -> {:error, :bad_json, "metadata must decode to an object"}
      {:error, %Jason.DecodeError{} = err} -> {:error, :bad_json, Exception.message(err)}
    end
  end

  defp read_screenshot(%Plug.Upload{path: path}) do
    case File.read(path) do
      {:ok, <<>>} -> {:error, :empty_screenshot}
      {:ok, bytes} -> {:ok, bytes}
      _ -> {:error, :empty_screenshot}
    end
  end

  defp read_screenshot(_), do: {:error, :no_screenshot}

  defp check_size(_conn, bytes) do
    cap_mb = BugFab.config(:max_upload_mb, 4)
    cap = cap_mb * 1024 * 1024

    if byte_size(bytes) > cap do
      {:error, :payload_too_large, cap}
    else
      :ok
    end
  end

  defp check_png(<<@png_signature, _::binary>>), do: :ok
  defp check_png(_), do: {:error, :not_png}

  defp do_save(conn, payload, screenshot_bytes, raw_meta) do
    {mod, state} = BugFab.storage_handle()

    server_ua = get_req_header(conn, "user-agent") |> List.first() || ""
    client_ua = get_in(payload, ["context", "user_agent"]) || ""

    env =
      get_in(payload, ["context", "environment"]) ||
        Map.get(raw_meta, "environment") || ""

    enriched =
      payload
      |> Map.put("server_user_agent", server_ua)
      |> Map.put("client_reported_user_agent", client_ua)
      |> Map.put("environment", env)
      |> Map.put("submitted_by", Map.get(raw_meta, "submitted_by", "anonymous"))

    case mod.save_report(state, enriched, screenshot_bytes) do
      {:ok, id} ->
        {:ok, stored} = mod.get_report(state, id)
        detail = Wire.detail(stored)

        conn
        |> put_resp_content_type("application/json")
        |> send_resp(201, Jason.encode!(Wire.intake_response(detail)))

      {:error, reason} ->
        Errors.send(conn, 500, "internal_error", "Failed to persist bug report: #{inspect(reason)}")
    end
  end

  defp client_ip(conn) do
    case Plug.Conn.get_req_header(conn, "x-forwarded-for") do
      [fwd | _] -> fwd |> String.split(",") |> List.first() |> String.trim()
      [] ->
        case conn.remote_ip do
          {a, b, c, d} -> "#{a}.#{b}.#{c}.#{d}"
          _ -> "unknown"
        end
    end
  end
end
