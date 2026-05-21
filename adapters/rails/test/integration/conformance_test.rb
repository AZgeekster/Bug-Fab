# frozen_string_literal: true

require "test_helper"

# Hits all 8 protocol endpoints in one test to verify conformance against
# the v0.1 wire spec. This is an in-process smoke test; the authoritative
# conformance suite is the Python pytest plugin shipped with the
# `bug-fab` package — adapter authors run it via:
#
#     pip install --pre bug-fab
#     pytest --bug-fab-conformance --base-url=http://localhost:3000/bug-fab
class BugFabConformanceTest < ActionDispatch::IntegrationTest
  include BugFabTestHelpers

  setup do
    BugFab::BugReportLifecycle.delete_all
    BugFab::BugReport.delete_all
    BugFab::BugReportIdCounter.delete_all
  end

  test "all 8 endpoints round-trip a report" do
    file = Rack::Test::UploadedFile.new(StringIO.new(fake_png_bytes("payload")), "image/png", original_filename: "s.png")

    # 1. POST /bug-reports
    post "/bug-fab/bug-reports",
         params: { metadata: valid_metadata.to_json, screenshot: file },
         headers: { "User-Agent" => "ServerUA-Conformance/1.0" }
    assert_response :created
    intake = JSON.parse(response.body)
    id = intake["id"]
    %w[id received_at stored_at github_issue_url].each { |k| assert intake.key?(k) }

    # 2. GET /reports
    get "/bug-fab/reports"
    assert_response :ok
    list = JSON.parse(response.body)
    assert_equal 1, list["total"]
    assert_equal id, list["items"].first["id"]
    %w[items total page page_size stats].each { |k| assert list.key?(k) }

    # 3. GET /reports/:id
    get "/bug-fab/reports/#{id}"
    assert_response :ok
    detail = JSON.parse(response.body)
    assert_equal id, detail["id"]
    assert_equal "0.1", detail["protocol_version"]
    assert_equal "ServerUA-Conformance/1.0", detail["server_user_agent"]
    assert_equal "Mozilla/5.0 ClientReported", detail["client_reported_user_agent"]
    assert_equal 1, detail["lifecycle"].size

    # 4. GET /reports/:id/screenshot
    get "/bug-fab/reports/#{id}/screenshot"
    assert_response :ok
    assert_equal "image/png", response.media_type

    # 5. PUT /reports/:id/status
    put "/bug-fab/reports/#{id}/status",
        params: { status: "fixed", fix_commit: "deadbeef", fix_description: "Patch shipped" }.to_json,
        headers: { "Content-Type" => "application/json" }
    assert_response :ok
    updated = JSON.parse(response.body)
    assert_equal "fixed", updated["status"]
    assert_equal 2, updated["lifecycle"].size

    # 6. POST /bulk-close-fixed
    post "/bug-fab/bulk-close-fixed"
    assert_response :ok
    assert_equal 1, JSON.parse(response.body)["closed"]

    # 7. POST /bulk-archive-closed
    post "/bug-fab/bulk-archive-closed"
    assert_response :ok
    assert_equal 1, JSON.parse(response.body)["archived"]

    # 8. DELETE /reports/:id (recreate first since archived rows still
    #    accept hard delete)
    file2 = Rack::Test::UploadedFile.new(StringIO.new(fake_png_bytes("payload2")), "image/png", original_filename: "s.png")
    post "/bug-fab/bug-reports",
         params: { metadata: valid_metadata.to_json, screenshot: file2 }
    assert_response :created
    new_id = JSON.parse(response.body)["id"]
    delete "/bug-fab/reports/#{new_id}"
    assert_response :no_content
  end

  test "deprecated-values rule on read paths" do
    # Submit and then directly inject a deprecated `resolved` status into
    # the row to simulate older data. The list and detail endpoints MUST
    # still surface it.
    md = BugFab::Validation.validate_create!(valid_metadata)
    record = BugFab::BugReport.create_from_payload!(md, fake_png_bytes, request_user_agent: "ServerUA/1")
    record.update_column(:status, "resolved")

    get "/bug-fab/reports/#{record.id}"
    assert_response :ok
    assert_equal "resolved", JSON.parse(response.body)["status"]

    get "/bug-fab/reports", params: { include_archived: "false" }
    assert_response :ok
    body = JSON.parse(response.body)
    assert_equal 1, body["total"]
    assert_equal "resolved", body["items"].first["status"]
  end
end
