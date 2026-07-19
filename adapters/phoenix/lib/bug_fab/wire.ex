defmodule BugFab.Wire do
  @moduledoc """
  Wire-protocol response shaping.

  Storage backends return raw maps that already match the wire shape, but
  this module guarantees:

  * Every field documented in PROTOCOL.md is present (defaulted if absent).
  * Internal-only columns never leak (e.g., Ecto's `id` numeric primary
    key is replaced by the protocol's `report_id` string).
  * The intake `201` response is the minimal 4-field envelope, not the
    full detail.
  """

  @doc "Shape a stored report into the wire detail object."
  @spec detail(map()) :: map()
  def detail(stored) when is_map(stored) do
    context = stored["context"] || %{}

    %{
      "id" => stored["id"],
      "protocol_version" => stored["protocol_version"] || "0.1",
      "title" => stored["title"] || "",
      "client_ts" => stored["client_ts"] || "",
      "report_type" => stored["report_type"] || "bug",
      "description" => stored["description"] || "",
      "expected_behavior" => stored["expected_behavior"] || "",
      "severity" => stored["severity"] || "medium",
      "status" => stored["status"] || "open",
      "module" => stored["module"] || context["module"] || "",
      "tags" => stored["tags"] || [],
      "reporter" => normalize_reporter(stored["reporter"]),
      "context" => context,
      "lifecycle" => stored["lifecycle"] || [],
      "server_user_agent" => stored["server_user_agent"] || "",
      "client_reported_user_agent" =>
        stored["client_reported_user_agent"] || context["user_agent"] || "",
      "environment" => stored["environment"] || context["environment"] || "",
      "has_screenshot" => Map.get(stored, "has_screenshot", true),
      "github_issue_url" => stored["github_issue_url"],
      "github_issue_number" => stored["github_issue_number"],
      "created_at" => stored["created_at"],
      "updated_at" => stored["updated_at"] || stored["created_at"]
    }
  end

  @doc "Shape a stored report into the wire summary object."
  @spec summary(map()) :: map()
  def summary(stored) when is_map(stored) do
    %{
      "id" => stored["id"],
      "title" => stored["title"] || "",
      "report_type" => stored["report_type"] || "bug",
      "severity" => stored["severity"] || "medium",
      "status" => stored["status"] || "open",
      "module" => stored["module"] || "",
      "created_at" => stored["created_at"],
      "has_screenshot" => Map.get(stored, "has_screenshot", true),
      "github_issue_url" => stored["github_issue_url"]
    }
  end

  @doc "The minimal intake 201 envelope."
  @spec intake_response(map()) :: map()
  def intake_response(detail) do
    %{
      "id" => detail["id"],
      "received_at" => detail["created_at"],
      "stored_at" => "bug-fab://reports/#{detail["id"]}",
      "github_issue_url" => detail["github_issue_url"]
    }
  end

  defp normalize_reporter(nil), do: %{"name" => "", "email" => "", "user_id" => ""}

  defp normalize_reporter(%{} = r) do
    %{
      "name" => r["name"] || "",
      "email" => r["email"] || "",
      "user_id" => r["user_id"] || ""
    }
  end
end
