defmodule BugFab.SchemasTest do
  use ExUnit.Case, async: true

  alias BugFab.Schemas

  describe "validate_create/1" do
    test "accepts a valid v0.1 payload" do
      meta = %{
        "protocol_version" => "0.1",
        "title" => "Save button is unresponsive",
        "client_ts" => "2026-04-27T15:29:58-07:00",
        "severity" => "high",
        "tags" => ["regression"]
      }

      assert {:ok, normalized} = Schemas.validate_create(meta)
      assert normalized["title"] == "Save button is unresponsive"
      assert normalized["severity"] == "high"
      assert normalized["report_type"] == "bug"
      assert normalized["reporter"]["name"] == ""
    end

    test "rejects unknown severity with 422" do
      meta = %{
        "protocol_version" => "0.1",
        "title" => "Bug",
        "client_ts" => "2026-04-27T15:29:58-07:00",
        "severity" => "urgent"
      }

      assert {:error, {:schema, cs}} = Schemas.validate_create(meta)
      errors = Schemas.format_errors(cs)
      assert Enum.any?(errors, fn e -> e.loc == "severity" end)
    end

    test "rejects missing protocol_version" do
      meta = %{"title" => "x", "client_ts" => "t"}
      assert {:error, :unsupported_protocol_version} = Schemas.validate_create(meta)
    end

    test "rejects wrong protocol_version" do
      meta = %{
        "protocol_version" => "0.2",
        "title" => "x",
        "client_ts" => "t"
      }

      assert {:error, :unsupported_protocol_version} = Schemas.validate_create(meta)
    end

    test "rejects unknown report_type with 422" do
      meta = %{
        "protocol_version" => "0.1",
        "title" => "x",
        "client_ts" => "t",
        "report_type" => "wishlist"
      }

      assert {:error, {:schema, _}} = Schemas.validate_create(meta)
    end

    test "rejects missing title" do
      meta = %{"protocol_version" => "0.1", "client_ts" => "t"}
      assert {:error, {:schema, cs}} = Schemas.validate_create(meta)
      errors = Schemas.format_errors(cs)
      assert Enum.any?(errors, fn e -> e.loc == "title" end)
    end

    test "preserves extra context keys verbatim" do
      meta = %{
        "protocol_version" => "0.1",
        "title" => "x",
        "client_ts" => "t",
        "context" => %{
          "url" => "https://example.com",
          "custom_field" => "preserved",
          "deep" => %{"a" => 1}
        }
      }

      assert {:ok, normalized} = Schemas.validate_create(meta)
      assert normalized["context"]["custom_field"] == "preserved"
      assert normalized["context"]["deep"] == %{"a" => 1}
    end

    test "rejects reporter fields longer than 256 chars" do
      long = String.duplicate("a", 257)

      meta = %{
        "protocol_version" => "0.1",
        "title" => "x",
        "client_ts" => "t",
        "reporter" => %{"name" => long}
      }

      assert {:error, {:schema, _}} = Schemas.validate_create(meta)
    end
  end

  describe "validate_status_update/1" do
    test "accepts valid status values" do
      for s <- ~w(open investigating fixed closed) do
        assert {:ok, %{"status" => ^s}} = Schemas.validate_status_update(%{"status" => s})
      end
    end

    test "rejects unknown status values" do
      assert {:error, %Ecto.Changeset{}} =
               Schemas.validate_status_update(%{"status" => "resolved"})
    end

    test "rejects missing status" do
      assert {:error, %Ecto.Changeset{}} = Schemas.validate_status_update(%{})
    end
  end
end
