defmodule BugFab.Repo.Migrations.CreateBugReports do
  @moduledoc """
  Creates the `bug_fab_reports` table used by `BugFab.Storage.EctoStorage`.

  Postgres-first; SQLite (`ecto_sqlite3`) is supported. The `:map` and
  `{:array, :map}` columns map to:

  * Postgres → `jsonb` / `jsonb[]`
  * SQLite   → `text` (Ecto serializes to JSON)

  Consumers running on databases other than Postgres / SQLite (MySQL,
  MSSQL) may need to adjust the column types — see `MIGRATION_NOTES.md`.

  ## Running

      mix ecto.migrate

  Or, when integrating into a host Phoenix application that already
  manages migrations, copy this file into the host's migration directory
  (the install generator will do that automatically once published).
  """
  use Ecto.Migration

  def change do
    create table(:bug_fab_reports) do
      add :report_id, :string, null: false
      add :protocol_version, :string, null: false, default: "0.1"
      add :title, :string, null: false
      add :client_ts, :string, null: false
      add :report_type, :string, null: false, default: "bug"
      add :description, :text, default: ""
      add :expected_behavior, :text, default: ""
      add :severity, :string, null: false, default: "medium"
      add :status, :string, null: false, default: "open"
      add :tags, {:array, :string}, default: []
      add :reporter, :map, default: %{}
      add :context, :map, default: %{}
      add :module_name, :string, default: ""
      add :server_user_agent, :string, default: ""
      add :client_reported_user_agent, :string, default: ""
      add :environment, :string, default: ""
      add :lifecycle, {:array, :map}, default: []
      add :github_issue_number, :integer
      add :github_issue_url, :string
      add :has_screenshot, :boolean, null: false, default: true
      add :created_at, :utc_datetime, null: false
      add :updated_at, :utc_datetime, null: false
      add :archived_at, :utc_datetime
    end

    create unique_index(:bug_fab_reports, [:report_id])
    create index(:bug_fab_reports, [:status])
    create index(:bug_fab_reports, [:severity])
    create index(:bug_fab_reports, [:environment])
    create index(:bug_fab_reports, [:archived_at])
    create index(:bug_fab_reports, [:created_at])
  end
end
