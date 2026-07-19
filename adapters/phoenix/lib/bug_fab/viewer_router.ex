defmodule BugFab.ViewerRouter do
  @moduledoc """
  Plug.Router for Bug-Fab's viewer + admin endpoints.

  Exposes the seven non-intake endpoints from PROTOCOL.md plus a minimal
  HTML index for sanity checking. The HTML viewer is intentionally
  minimal — a single embedded template suitable for hobby / internal-tools
  deployments. Real consumers will typically replace the HTML view with a
  Phoenix LiveView page; the JSON API endpoints (`/reports`, etc.) cover
  every interaction the LiveView needs.

  ## Mounting

      forward "/admin/bug-reports", BugFab.ViewerRouter

  ## Permissions

  Three viewer permissions gate write endpoints, read from
  `config :bug_fab, :viewer_permissions`:

  * `can_edit_status` — `PUT /reports/:id/status`
  * `can_delete` — `DELETE /reports/:id`
  * `can_bulk` — `POST /bulk-close-fixed`, `POST /bulk-archive-closed`

  A disabled permission returns `403` with the standard error envelope.
  """

  use Plug.Router

  alias BugFab.{Errors, Schemas, Wire}

  @report_id_regex ~r/^bug-[A-Za-z]?\d{1,12}$/

  plug Plug.Parsers,
    parsers: [:json],
    pass: ["*/*"],
    json_decoder: Jason

  plug :match
  plug :dispatch

  # ----- HTML index (minimal sanity-check page) -----

  get "/" do
    {mod, state} = BugFab.storage_handle()
    {:ok, %{items: items}} = mod.list_reports(state, %{}, 1, 50)
    stats = mod.list_stats(state)
    html = render_index(items, stats)

    conn
    |> put_resp_content_type("text/html; charset=utf-8")
    |> send_resp(200, html)
  end

  # ----- JSON list -----

  get "/reports" do
    conn = fetch_query_params(conn)
    params = conn.query_params

    page = to_int(params["page"], 1)
    page_size = to_int(params["page_size"], 20) |> min(200) |> max(1)

    filters =
      %{
        "status" => params["status"],
        "severity" => params["severity"],
        "environment" => params["environment"],
        "module" => params["module"]
      }
      |> Enum.reject(fn {_k, v} -> is_nil(v) or v == "" end)
      |> Map.new()

    {mod, state} = BugFab.storage_handle()
    {:ok, %{items: items, total: total}} = mod.list_reports(state, filters, page, page_size)
    stats = mod.list_stats(state)

    body = %{
      "items" => Enum.map(items, &Wire.summary/1),
      "total" => total,
      "page" => page,
      "page_size" => page_size,
      "stats" => Map.take(stats, ~w(open investigating fixed closed))
    }

    json(conn, 200, body)
  end

  # ----- JSON detail -----

  get "/reports/:id" do
    if valid_id?(id) do
      {mod, state} = BugFab.storage_handle()

      case mod.get_report(state, id) do
        {:ok, stored} -> json(conn, 200, Wire.detail(stored))
        {:error, :not_found} -> not_found(conn)
      end
    else
      not_found(conn)
    end
  end

  # ----- Screenshot -----

  get "/reports/:id/screenshot" do
    if valid_id?(id) do
      {mod, state} = BugFab.storage_handle()

      case mod.get_screenshot(state, id) do
        {:ok, bytes} ->
          conn
          |> put_resp_content_type("image/png")
          |> send_resp(200, bytes)

        {:error, :not_found} ->
          not_found(conn)
      end
    else
      not_found(conn)
    end
  end

  # ----- Status update -----

  put "/reports/:id/status" do
    cond do
      not valid_id?(id) ->
        not_found(conn)

      true ->
        case check_permission(:can_edit_status) do
          {:error, :forbidden, flag} ->
            Errors.send(conn, 403, "validation_error", "viewer action '#{flag}' is disabled")

          :ok ->
            case Schemas.validate_status_update(conn.body_params || %{}) do
              {:error, %Ecto.Changeset{} = cs} ->
                Errors.send(conn, 422, "schema_error", Schemas.format_errors(cs))

              {:ok, payload} ->
                {mod, state} = BugFab.storage_handle()
                actor = resolve_actor(conn)

                case mod.update_status(state, id, payload, by: actor) do
                  {:ok, stored} -> json(conn, 200, Wire.detail(stored))
                  {:error, :not_found} -> not_found(conn)
                end
            end
        end
    end
  end

  # ----- Delete -----

  delete "/reports/:id" do
    cond do
      not valid_id?(id) ->
        not_found(conn)

      true ->
        case check_permission(:can_delete) do
          {:error, :forbidden, flag} ->
            Errors.send(conn, 403, "validation_error", "viewer action '#{flag}' is disabled")

          :ok ->
            {mod, state} = BugFab.storage_handle()

            case mod.delete_report(state, id) do
              :ok -> send_resp(conn, 204, "")
              {:error, :not_found} -> not_found(conn)
            end
        end
    end
  end

  # ----- Bulk close -----

  post "/bulk-close-fixed" do
    case check_permission(:can_bulk) do
      :ok ->
        {mod, state} = BugFab.storage_handle()
        actor = resolve_actor(conn)
        {:ok, n} = mod.bulk_close_fixed(state, by: actor)
        json(conn, 200, %{"closed" => n})

      {:error, :forbidden, flag} ->
        Errors.send(conn, 403, "validation_error", "viewer action '#{flag}' is disabled")
    end
  end

  # ----- Bulk archive -----

  post "/bulk-archive-closed" do
    case check_permission(:can_bulk) do
      :ok ->
        {mod, state} = BugFab.storage_handle()
        {:ok, n} = mod.bulk_archive_closed(state)
        json(conn, 200, %{"archived" => n})

      {:error, :forbidden, flag} ->
        Errors.send(conn, 403, "validation_error", "viewer action '#{flag}' is disabled")
    end
  end

  match _ do
    Errors.send(conn, 404, "validation_error", "Not Found")
  end

  # ----- helpers -----

  defp valid_id?(id), do: is_binary(id) and Regex.match?(@report_id_regex, id)

  defp not_found(conn), do: Errors.send(conn, 404, "validation_error", "Bug report not found")

  defp json(conn, status, body) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, Jason.encode!(body))
  end

  defp check_permission(flag) do
    perms =
      BugFab.config(:viewer_permissions, %{
        can_edit_status: true,
        can_delete: true,
        can_bulk: true
      })

    if Map.get(perms, flag, false), do: :ok, else: {:error, :forbidden, flag}
  end

  defp resolve_actor(conn) do
    case BugFab.config(:actor_resolver) do
      fun when is_function(fun, 1) ->
        try do
          fun.(conn) || "viewer"
        rescue
          _ -> "viewer"
        end

      _ ->
        "viewer"
    end
  end

  defp to_int(nil, default), do: default
  defp to_int(s, default) when is_binary(s) do
    case Integer.parse(s) do
      {n, _} -> n
      _ -> default
    end
  end
  defp to_int(n, _) when is_integer(n), do: n
  defp to_int(_, default), do: default

  # Minimal HTML — see module doc for LiveView replacement guidance.
  defp render_index(items, stats) do
    rows =
      items
      |> Enum.map(fn r ->
        """
        <tr>
          <td><a href="reports/#{r["id"]}">#{r["id"]}</a></td>
          <td>#{html_escape(r["title"])}</td>
          <td>#{r["severity"]}</td>
          <td>#{r["status"]}</td>
          <td>#{r["created_at"]}</td>
        </tr>
        """
      end)
      |> Enum.join()

    """
    <!doctype html>
    <html><head><meta charset="utf-8"><title>Bug-Fab Viewer</title>
    <style>
      body{font-family:system-ui,sans-serif;margin:2rem;background:#fafafa}
      h1{margin-top:0}
      .stats{display:flex;gap:1rem;margin-bottom:1rem}
      .stat{padding:.5rem 1rem;background:#fff;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
      table{width:100%;border-collapse:collapse;background:#fff}
      th,td{padding:.5rem;border-bottom:1px solid #eee;text-align:left}
      th{background:#f4f4f4}
    </style></head><body>
    <h1>Bug-Fab Viewer</h1>
    <div class="stats">
      <div class="stat">open: #{stats["open"] || 0}</div>
      <div class="stat">investigating: #{stats["investigating"] || 0}</div>
      <div class="stat">fixed: #{stats["fixed"] || 0}</div>
      <div class="stat">closed: #{stats["closed"] || 0}</div>
    </div>
    <table>
      <thead><tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead>
      <tbody>#{rows}</tbody>
    </table>
    </body></html>
    """
  end

  defp html_escape(nil), do: ""
  defp html_escape(s) when is_binary(s) do
    s
    |> String.replace("&", "&amp;")
    |> String.replace("<", "&lt;")
    |> String.replace(">", "&gt;")
    |> String.replace("\"", "&quot;")
  end
end
