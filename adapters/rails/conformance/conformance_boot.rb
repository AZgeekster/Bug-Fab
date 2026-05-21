# frozen_string_literal: true

# Boots the in-repo dummy Rails app (test/dummy/) with conformance-friendly
# tweaks applied at runtime — no edits to test/dummy/ on disk.
#
# Runtime tweaks applied here (NOT baked into the dummy app):
#   1. BugFab.configuration.storage_root -> a writable tmp dir
#      (default test/dummy/storage/ is on the read-only mount).
#   2. ActiveRecord database URL -> writable tmp sqlite3 file
#      (overrides test/dummy/config/database.yml at the env-var level).
#   3. db:schema:load -> populates the engine's tables from the
#      committed schema.rb. Idempotent across container restarts.
#
# Then boots Puma against the Rack app on 0.0.0.0:8080.
#
# Invoked from docker-compose.yml as:
#   bundle exec ruby /src/adapters/rails/conformance/conformance_boot.rb

require "fileutils"

# --- 1) Writable storage for screenshots --------------------------------------
storage_dir = ENV.fetch("BUG_FAB_STORAGE_DIR", "/tmp/bug-fab-conformance-storage")
FileUtils.mkdir_p(storage_dir)

# --- 2) Writable sqlite3 file (DATABASE_URL beats database.yml) ---------------
db_path = ENV.fetch("BUG_FAB_CONFORMANCE_DB", "/tmp/bug-fab-conformance.sqlite3")
ENV["DATABASE_URL"] ||= "sqlite3:#{db_path}"
File.delete(db_path) if File.exist?(db_path) && ENV["BUG_FAB_CONFORMANCE_RESET_DB"] != "false"

# Use "test" env: the dummy app's development.rb references `config.assets`
# which would need sprockets-rails (a dev-only gem that's not in ../Gemfile).
# The test env config is minimal and the engine's runtime behavior is identical.
# Production would need SECRET_KEY_BASE wiring we don't want to bother with for
# a throwaway conformance run.
ENV["RAILS_ENV"] ||= "test"

# --- 3) Boot the dummy Rails app ---------------------------------------------
# Resolve test/dummy/config/environment relative to THIS file's parent dir
# (i.e. /src/adapters/rails/conformance/ -> /src/adapters/rails/test/dummy/).
dummy_env = File.expand_path("../test/dummy/config/environment.rb", __dir__)
require dummy_env

# --- 4) Apply storage_root override AFTER Rails boots so we win over any -----
#       no-op initializer the dummy app might add later.
BugFab.configure do |c|
  c.storage_root = storage_dir
  c.github_enabled = false
end

# --- 5) Populate the schema (engine tables) from the committed schema.rb. ----
ActiveRecord::Base.establish_connection
ActiveRecord::Schema.verbose = false
schema_path = File.expand_path("../test/dummy/db/schema.rb", __dir__)
load schema_path

# --- 6) Hand off to Puma -----------------------------------------------------
require "rack"
require "rack/handler/puma"

port = Integer(ENV.fetch("PORT", "8080"))
host = ENV.fetch("HOST", "0.0.0.0")

puts "[conformance] bug_fab-rails booted — POST http://#{host}:#{port}/bug-fab/bug-reports"
puts "[conformance] storage_root=#{storage_dir} db=#{db_path}"

Rack::Handler::Puma.run(
  Rails.application,
  Host: host,
  Port: port,
  Silent: false,
  Threads: "0:5"
)
