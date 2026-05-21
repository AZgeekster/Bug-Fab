# frozen_string_literal: true

# Initial schema for the bug_fab-rails engine.
#
# Schema mirrors `repo/bug_fab/storage/_models.py:BugReportORM` from the
# Python reference adapter. Key shape rules:
#
#   * IDs are strings (`bug-NNN` format), not integer primary keys.
#   * Screenshots live on disk; `screenshot_path` is the only DB pointer.
#   * The full original wire metadata round-trips verbatim in
#     `metadata_json` (text). Typed columns above it are denormalized for
#     indexable queries (severity, status, environment, etc.).
#   * The `bug_fab_id_counter` table replaces dialect-specific sequences
#     so SQLite / Postgres / MySQL all behave the same way.
class CreateBugFabTables < ActiveRecord::Migration[7.1]
  def change
    create_table :bug_fab_bug_reports, id: false do |t|
      t.string :id, primary_key: true, null: false
      t.string :protocol_version, null: false, default: "0.1"
      t.string :title, null: false, limit: 200
      t.text :description, null: false, default: ""
      t.string :report_type, null: false, default: "bug"
      t.string :severity, null: true
      t.string :status, null: false, default: "open"
      t.string :environment
      t.string :module_name
      t.string :page_url, limit: 2048
      t.string :app_version
      t.string :reporter_email, limit: 256
      t.string :reporter_name, limit: 256
      t.string :reporter_user_id, limit: 256
      t.string :client_ts
      t.text :server_user_agent
      t.text :client_reported_user_agent
      t.string :screenshot_path, null: false
      t.text :metadata_json, null: false
      t.string :github_issue_url
      t.integer :github_issue_number
      t.datetime :received_at, null: false
      t.datetime :updated_at_protocol
      t.datetime :archived_at
    end

    add_index :bug_fab_bug_reports, :received_at
    add_index :bug_fab_bug_reports, :status
    add_index :bug_fab_bug_reports, :severity
    add_index :bug_fab_bug_reports, :environment
    add_index :bug_fab_bug_reports, :archived_at
    add_index :bug_fab_bug_reports, :module_name

    create_table :bug_fab_lifecycle_events do |t|
      t.string :bug_report_id, null: false
      t.string :action, null: false
      t.string :by
      t.datetime :at, null: false
      t.string :fix_commit
      t.text :fix_description
    end

    add_index :bug_fab_lifecycle_events, :bug_report_id
    add_index :bug_fab_lifecycle_events, :at
    add_foreign_key :bug_fab_lifecycle_events,
                    :bug_fab_bug_reports,
                    column: :bug_report_id,
                    primary_key: :id,
                    on_delete: :cascade

    create_table :bug_fab_id_counter, id: false do |t|
      t.integer :id, primary_key: true, null: false
      t.integer :last_value, null: false, default: 0
    end
  end
end
