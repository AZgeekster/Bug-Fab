defmodule BugFab.Errors do
  @moduledoc """
  Helpers that build the protocol's standard JSON error envelope.

      {
        "error": "validation_error",
        "detail": "metadata is not valid JSON"
      }

  All non-2xx responses (except `204` and the `image/png` 404) use this
  shape.
  """

  import Plug.Conn

  @spec send(Plug.Conn.t(), pos_integer(), String.t(), term(), keyword()) :: Plug.Conn.t()
  def send(conn, status, code, detail, extra \\ []) do
    body =
      %{"error" => code, "detail" => detail}
      |> Map.merge(Map.new(extra, fn {k, v} -> {to_string(k), v} end))

    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, Jason.encode!(body))
    |> halt()
  end
end
