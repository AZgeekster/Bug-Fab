defmodule BugFab.FileStorageTest do
  use ExUnit.Case, async: false

  alias BugFab.Storage.FileStorage
  alias BugFab.Test.Fixtures

  setup do
    dir = Fixtures.tmp_storage_dir()
    handle = FileStorage.handle(storage_dir: dir, id_prefix: "")
    on_exit(fn -> File.rm_rf!(dir) end)
    {:ok, handle: handle, dir: dir}
  end

  test "save_report writes screenshot + JSON and assigns sequential ids", %{handle: h} do
    {:ok, id1} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())
    {:ok, id2} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())

    assert id1 == "bug-001"
    assert id2 == "bug-002"

    {:ok, stored} = FileStorage.get_report(h, id1)
    assert stored["title"] == "Save button is unresponsive"
    assert stored["status"] == "open"
    assert stored["lifecycle"] |> hd() |> Map.get("action") == "created"
  end

  test "list_reports filters and paginates", %{handle: h} do
    for sev <- ~w(low medium high critical) do
      {:ok, _} =
        FileStorage.save_report(h, build_meta(%{"severity" => sev}), Fixtures.tiny_png())
    end

    {:ok, %{items: items, total: total}} =
      FileStorage.list_reports(h, %{"severity" => "high"}, 1, 20)

    assert total == 1
    assert hd(items)["severity"] == "high"
  end

  test "update_status appends lifecycle and persists", %{handle: h} do
    {:ok, id} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())

    {:ok, updated} =
      FileStorage.update_status(h, id, %{"status" => "fixed", "fix_commit" => "abc123"},
        by: "tester"
      )

    assert updated["status"] == "fixed"
    last = List.last(updated["lifecycle"])
    assert last["action"] == "status_changed"
    assert last["status"] == "fixed"
    assert last["fix_commit"] == "abc123"
    assert last["by"] == "tester"
  end

  test "get_screenshot returns the stored bytes", %{handle: h} do
    {:ok, id} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())
    {:ok, bytes} = FileStorage.get_screenshot(h, id)
    assert bytes == Fixtures.tiny_png()
  end

  test "delete_report removes report + screenshot", %{handle: h} do
    {:ok, id} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())
    assert :ok = FileStorage.delete_report(h, id)
    assert {:error, :not_found} = FileStorage.get_report(h, id)
    assert {:error, :not_found} = FileStorage.get_screenshot(h, id)
  end

  test "bulk_close_fixed transitions every fixed report to closed", %{handle: h} do
    {:ok, id1} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())
    {:ok, id2} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())
    {:ok, _} = FileStorage.update_status(h, id1, %{"status" => "fixed"}, by: "x")
    {:ok, _} = FileStorage.update_status(h, id2, %{"status" => "fixed"}, by: "x")

    {:ok, n} = FileStorage.bulk_close_fixed(h, by: "x")
    assert n == 2

    {:ok, r1} = FileStorage.get_report(h, id1)
    assert r1["status"] == "closed"
  end

  test "bulk_archive_closed moves closed reports to archive dir", %{handle: h, dir: dir} do
    {:ok, id} = FileStorage.save_report(h, build_meta(), Fixtures.tiny_png())
    {:ok, _} = FileStorage.update_status(h, id, %{"status" => "closed"}, by: "x")
    {:ok, n} = FileStorage.bulk_archive_closed(h)
    assert n == 1
    refute File.exists?(Path.join(dir, "#{id}.json"))
    assert File.exists?(Path.join([dir, "archive", "#{id}.json"]))
  end

  defp build_meta(overrides \\ %{}) do
    Fixtures.metadata(overrides)
    |> Map.put("server_user_agent", "test-agent")
    |> Map.put("client_reported_user_agent", "test-agent")
    |> Map.put("environment", "test")
  end
end
