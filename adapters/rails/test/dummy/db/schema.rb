# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[7.1].define(version: 2026_05_01_000001) do
  create_table "bug_fab_bug_reports", id: :string, force: :cascade do |t|
    t.string "protocol_version", default: "0.1", null: false
    t.string "title", limit: 200, null: false
    t.text "description", default: "", null: false
    t.string "report_type", default: "bug", null: false
    t.string "severity"
    t.string "status", default: "open", null: false
    t.string "environment"
    t.string "module_name"
    t.string "page_url", limit: 2048
    t.string "app_version"
    t.string "reporter_email", limit: 256
    t.string "reporter_name", limit: 256
    t.string "reporter_user_id", limit: 256
    t.string "client_ts"
    t.text "server_user_agent"
    t.text "client_reported_user_agent"
    t.string "screenshot_path", null: false
    t.text "metadata_json", null: false
    t.string "github_issue_url"
    t.integer "github_issue_number"
    t.datetime "received_at", null: false
    t.datetime "updated_at_protocol"
    t.datetime "archived_at"
    t.index ["archived_at"], name: "index_bug_fab_bug_reports_on_archived_at"
    t.index ["environment"], name: "index_bug_fab_bug_reports_on_environment"
    t.index ["module_name"], name: "index_bug_fab_bug_reports_on_module_name"
    t.index ["received_at"], name: "index_bug_fab_bug_reports_on_received_at"
    t.index ["severity"], name: "index_bug_fab_bug_reports_on_severity"
    t.index ["status"], name: "index_bug_fab_bug_reports_on_status"
  end

  create_table "bug_fab_id_counter", force: :cascade do |t|
    t.integer "last_value", default: 0, null: false
  end

  create_table "bug_fab_lifecycle_events", force: :cascade do |t|
    t.string "bug_report_id", null: false
    t.string "action", null: false
    t.string "by"
    t.datetime "at", null: false
    t.string "fix_commit"
    t.text "fix_description"
    t.index ["at"], name: "index_bug_fab_lifecycle_events_on_at"
    t.index ["bug_report_id"], name: "index_bug_fab_lifecycle_events_on_bug_report_id"
  end

  add_foreign_key "bug_fab_lifecycle_events", "bug_fab_bug_reports", column: "bug_report_id", on_delete: :cascade
end
