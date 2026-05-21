# frozen_string_literal: true

require "test_helper"

class BugFab::BulkActionsControllerTest < ActionDispatch::IntegrationTest
  include BugFabTestHelpers

  setup do
    BugFab::BugReportLifecycle.delete_all
    BugFab::BugReport.delete_all
    BugFab::BugReportIdCounter.delete_all
  end

  def submit_and_set_status(status)
    md = BugFab::Validation.validate_create!(valid_metadata)
    r = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "test")
    r.apply_status_update!(status: status, fix_commit: "", fix_description: "", by: "x") unless status == "open"
    r
  end

  test "POST /bulk-close-fixed transitions fixed → closed" do
    submit_and_set_status("fixed")
    submit_and_set_status("fixed")
    submit_and_set_status("open")

    post "/bug-fab/bulk-close-fixed"
    assert_response :ok
    body = JSON.parse(response.body)
    assert_equal 2, body["closed"]
  end

  test "POST /bulk-archive-closed sets archived_at and excludes from list" do
    submit_and_set_status("closed")
    submit_and_set_status("closed")

    post "/bug-fab/bulk-archive-closed"
    assert_response :ok
    body = JSON.parse(response.body)
    assert_equal 2, body["archived"]

    get "/bug-fab/reports"
    assert_response :ok
    list = JSON.parse(response.body)
    assert_equal 0, list["total"], "archived rows must be hidden by default"

    get "/bug-fab/reports", params: { include_archived: "true" }
    assert_response :ok
    list = JSON.parse(response.body)
    assert_equal 2, list["total"]
  end

  test "bulk endpoints respect can_bulk = false" do
    BugFab.configuration.viewer_permissions[:can_bulk] = false
    post "/bug-fab/bulk-close-fixed"
    assert_response :forbidden
  ensure
    BugFab.configuration.viewer_permissions[:can_bulk] = true
  end
end
