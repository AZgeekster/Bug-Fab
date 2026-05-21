# frozen_string_literal: true

require "test_helper"

class BugFab::BugReportTest < ActiveSupport::TestCase
  include BugFabTestHelpers

  setup do
    BugFab::BugReportLifecycle.delete_all
    BugFab::BugReport.delete_all
    BugFab::BugReportIdCounter.delete_all
  end

  test "create_from_payload! mints sequential bug-NNN IDs" do
    md1 = BugFab::Validation.validate_create!(valid_metadata)
    r1  = BugFab::BugReport.create_from_payload!(md1, fake_png_bytes, request_user_agent: "ServerUA/1")
    md2 = BugFab::Validation.validate_create!(valid_metadata)
    r2  = BugFab::BugReport.create_from_payload!(md2, fake_png_bytes, request_user_agent: "ServerUA/1")

    assert_match(/\Abug-\d{3,}\z/, r1.id)
    assert_match(/\Abug-\d{3,}\z/, r2.id)
    refute_equal r1.id, r2.id
  end

  test "create_from_payload! captures dual user-agent values" do
    md = BugFab::Validation.validate_create!(valid_metadata)
    report = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "ServerUA/1.0")

    assert_equal "ServerUA/1.0",          report.server_user_agent
    assert_equal "Mozilla/5.0 ClientReported", report.client_reported_user_agent
  end

  test "apply_status_update! appends a lifecycle entry" do
    md = BugFab::Validation.validate_create!(valid_metadata)
    report = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "ServerUA/1")

    assert_equal 1, report.lifecycle_events.count
    assert_equal "created", report.lifecycle_events.first.action

    report.apply_status_update!(status: "fixed", fix_commit: "abc123", fix_description: "Patched.", by: "alice")

    assert_equal "fixed", report.reload.status
    assert_equal 2, report.lifecycle_events.count
    last = report.lifecycle_events.last
    assert_equal "status_changed", last.action
    assert_equal "alice",          last.by
    assert_equal "abc123",         last.fix_commit
  end

  test "apply_status_update! rejects unknown statuses" do
    md = BugFab::Validation.validate_create!(valid_metadata)
    report = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "ServerUA/1")

    assert_raises(ArgumentError) do
      report.apply_status_update!(status: "urgent", fix_commit: "", fix_description: "", by: "alice")
    end
  end

  test "bulk_close_fixed! transitions every fixed report to closed" do
    md = BugFab::Validation.validate_create!(valid_metadata)
    a = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "ServerUA/1")
    b = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "ServerUA/1")
    a.apply_status_update!(status: "fixed", fix_commit: "", fix_description: "", by: "x")
    b.apply_status_update!(status: "fixed", fix_commit: "", fix_description: "", by: "x")

    closed = BugFab::BugReport.bulk_close_fixed!(by: "ops")
    assert_equal 2, closed
    assert_equal ["closed", "closed"], BugFab::BugReport.order(:id).pluck(:status)
  end
end
