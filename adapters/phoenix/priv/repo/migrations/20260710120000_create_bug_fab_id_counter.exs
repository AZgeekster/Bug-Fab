defmodule BugFab.Repo.Migrations.CreateBugFabIdCounter do
  @moduledoc """
  Creates the single-row `bug_fab_id_counter` table used to mint sequential
  `bug-NNN` report ids.

  ## Why a counter row rather than `COUNT(*) + 1`

  `EctoStorage.save_report/3` previously derived the next id from a live row
  count. Counting is not allocation: delete `bug-001` from a table of three
  reports and the next insert computes `2 + 1 = 3`, colliding with the
  existing `bug-003` on the unique index. The report is lost.

  The counter is monotonic and never reused, so a delete cannot rewind it.

  ## Why an atomic `UPDATE`, not `SELECT ... FOR UPDATE`

  This adapter ships `ecto_sqlite3` alongside `postgrex`, and SQLite has no
  row locks -- `FOR UPDATE` is a *syntax error* there, not a no-op. A single
  `UPDATE ... SET last_value = last_value + 1` is the only formulation
  portable across both: SQLite serializes writers so the increment cannot be
  lost, and Postgres takes a row lock for the duration of the statement.

  This mirrors the Python reference (`bug_fab/storage/_sql_base.py`) and the
  Rails adapter (`BugFab::BugReportIdCounter`).

  ## Running

      mix ecto.migrate

  Hosts that manage their own migrations should copy this file alongside
  `..._create_bug_reports.exs`; the two must both be applied.
  """
  use Ecto.Migration

  def change do
    create table(:bug_fab_id_counter, primary_key: false) do
      add :id, :integer, primary_key: true
      add :last_value, :bigint, null: false, default: 0
    end

    # Seed the single row the allocator increments. `save_report/3` assumes it
    # exists; without this the first submission would update zero rows and
    # read back nil.
    execute(
      "INSERT INTO bug_fab_id_counter (id, last_value) VALUES (1, 0)",
      "DELETE FROM bug_fab_id_counter WHERE id = 1"
    )
  end
end
