defmodule BugFab.EctoStorageTest do
  @moduledoc """
  Regression tests for `BugFab.Storage.EctoStorage`'s report-id allocator.

  `save_report/3` used to derive the next id from `repo.aggregate(BugReport,
  :count, :id) + 1`, beneath a comment claiming the transaction prevented
  collisions. Counting is not allocation: delete `bug-001` from three reports
  and the next insert computes `2 + 1 = 3`, colliding with the live `bug-003`
  on the unique index.

  This module was the adapter's first `EctoStorage` test. There were none, so
  nothing exercised the allocator at all.

  Runs on SQLite (`ecto_sqlite3`) deliberately -- it is the backend that makes
  `SELECT ... FOR UPDATE` unusable, which is why the allocator increments a
  counter row with a single atomic `UPDATE` instead.
  """
  use ExUnit.Case, async: false

  alias BugFab.Storage.EctoStorage

  defmodule TestRepo do
    @moduledoc false
    use Ecto.Repo, otp_app: :bug_fab, adapter: Ecto.Adapters.SQLite3
  end

  @screenshot_dir Path.join(System.tmp_dir!(), "bug_fab_ecto_test_shots")

  setup do
    db = Path.join(System.tmp_dir!(), "bug_fab_ecto_test_#{System.unique_integer([:positive])}.db")
    File.rm_rf!(@screenshot_dir)

    Application.put_env(:bug_fab, TestRepo, database: db, pool_size: 1, journal_mode: :wal)
    {:ok, pid} = TestRepo.start_link()

    # Both migrations, in order. The counter table is required by save_report/3.
    Ecto.Migrator.run(TestRepo, migrations_path(), :up, all: true, log: false)

    handle = EctoStorage.handle(repo: TestRepo, screenshot_dir: @screenshot_dir)

    on_exit(fn ->
      # The repo supervisor exits `:shutdown`, which `Supervisor.stop/1`
      # re-raises as a test failure. Teardown noise must not be able to fail
      # an otherwise-passing assertion.
      try do
        if Process.alive?(pid), do: Supervisor.stop(pid, :normal, 5_000)
      catch
        :exit, _ -> :ok
      end

      File.rm_rf(db)
      File.rm_rf(@screenshot_dir)
    end)

    {:ok, handle: handle}
  end

  defp migrations_path do
    Path.expand("../../priv/repo/migrations", __DIR__)
  end

  defp metadata(title) do
    %{
      "protocol_version" => "0.1",
      "title" => title,
      "client_ts" => "2026-07-10T00:00:00Z",
      "severity" => "medium",
      "context" => %{}
    }
  end

  defp png, do: <<137, 80, 78, 71, 13, 10, 26, 10>>

  defp save!(handle, title) do
    {:ok, id} = EctoStorage.save_report(handle, metadata(title), png())
    id
  end

  test "allocates sequential ids", %{handle: handle} do
    assert save!(handle, "one") == "bug-001"
    assert save!(handle, "two") == "bug-002"
    assert save!(handle, "three") == "bug-003"
  end

  test "a delete does not rewind the allocator", %{handle: handle} do
    first = save!(handle, "one")
    save!(handle, "two")
    third = save!(handle, "three")

    :ok = drop(handle, first)

    # With COUNT(*) + 1 this returns "bug-003", colliding with `third` on the
    # unique index and losing the report.
    fourth = save!(handle, "four")

    refute fourth == third
    assert fourth == "bug-004"
  end

  test "ids are never reused after deleting the most recent report", %{handle: handle} do
    save!(handle, "one")
    second = save!(handle, "two")
    :ok = drop(handle, second)

    assert save!(handle, "three") == "bug-003"
  end

  test "sequence survives deleting every report", %{handle: handle} do
    for t <- ["one", "two", "three"], do: save!(handle, t)
    for id <- ["bug-001", "bug-002", "bug-003"], do: :ok = drop(handle, id)

    # An empty table means COUNT(*) + 1 == 1, silently recycling `bug-001`
    # for a different report. The counter row has no such amnesia.
    assert save!(handle, "four") == "bug-004"
  end

  defp drop(handle, report_id) do
    case EctoStorage.delete_report(handle, report_id) do
      :ok -> :ok
      true -> :ok
      {:ok, _} -> :ok
      other -> flunk("delete_report returned #{inspect(other)}")
    end
  end
end
