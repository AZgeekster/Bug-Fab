# frozen_string_literal: true

# Canonical Rails Engine test harness, scaffolded by `rails plugin new
# bug_fab-rails --mountable --dummy-path=test/dummy` and ported in on
# 2026-05-04. The previous hand-rolled inline `TestApplication` pattern
# double-evaluated engine routes during `Rails.application.initialize!`
# and couldn't boot on Rails 7.1 or 7.2; the dummy-app pattern is the
# standard Rails-Engine convention and avoids the issue entirely.

ENV["RAILS_ENV"] = "test"

require_relative "dummy/config/environment"
ActiveRecord::Migrator.migrations_paths = [File.expand_path("dummy/db/migrate", __dir__)]
ActiveRecord::Migrator.migrations_paths << File.expand_path("../db/migrate", __dir__)
require "rails/test_help"

require "minitest/autorun"
begin
  require "minitest/reporters"
  Minitest::Reporters.use!(Minitest::Reporters::DefaultReporter.new(color: true))
rescue LoadError
  # reporters are optional; default minitest output is fine
end

require "tmpdir"
require "fileutils"

# Configure Bug-Fab storage to a fresh tmpdir for the test run.
storage_root = Pathname.new(Dir.mktmpdir("bug_fab_test_storage"))
BugFab.configure do |config|
  config.storage_root = storage_root
  config.github_enabled = false
end

# Helpers shared across tests.
module BugFabTestHelpers
  PNG_MAGIC = "\x89PNG\r\n\x1a\n".b

  # Minimal valid PNG: magic bytes plus a stub. The intake validator only
  # checks the leading signature, so this passes the magic-byte gate.
  def fake_png_bytes(payload = "stub")
    PNG_MAGIC + payload.b
  end

  def valid_metadata(overrides = {})
    {
      "protocol_version" => "0.1",
      "title" => "Save button is unresponsive",
      "client_ts" => "2026-04-27T15:29:58-07:00",
      "report_type" => "bug",
      "severity" => "high",
      "tags" => ["regression"],
      "reporter" => { "email" => "alice@example.com" },
      "context" => {
        "url" => "https://example.com/cart",
        "module" => "checkout",
        "user_agent" => "Mozilla/5.0 ClientReported",
        "viewport_width" => 1920,
        "viewport_height" => 1080,
        "app_version" => "1.4.2",
        "environment" => "prod"
      }
    }.merge(overrides)
  end
end
