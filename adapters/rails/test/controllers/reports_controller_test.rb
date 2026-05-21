# frozen_string_literal: true

require "test_helper"

# Action-level controller tests for `POST /bug-reports` and the viewer
# JSON endpoints. Conformance-style end-to-end coverage lives in
# `test/integration/conformance_test.rb`.
class BugFab::ReportsControllerTest < ActionDispatch::IntegrationTest
  include BugFabTestHelpers

  setup do
    BugFab::BugReportLifecycle.delete_all
    BugFab::BugReport.delete_all
    BugFab::BugReportIdCounter.delete_all
  end

  def post_intake(metadata: valid_metadata, screenshot_bytes: fake_png_bytes, ua: "ServerUA/1.0")
    file = Rack::Test::UploadedFile.new(StringIO.new(screenshot_bytes), "image/png", original_filename: "s.png")
    post "/bug-fab/bug-reports",
         params: { metadata: metadata.to_json, screenshot: file },
         headers: { "User-Agent" => ua }
  end

  test "POST /bug-reports persists a report and returns 201 envelope" do
    post_intake
    assert_response :created
    body = JSON.parse(response.body)
    assert_match(/\Abug-\d{3,}\z/, body["id"])
    assert body["received_at"].present?
    assert body["stored_at"].start_with?("bug-fab://reports/")
    assert_nil body["github_issue_url"]
  end

  test "POST /bug-reports rejects bad protocol_version with 400" do
    post_intake(metadata: valid_metadata("protocol_version" => "9.9"))
    assert_response :bad_request
    body = JSON.parse(response.body)
    assert_equal "unsupported_protocol_version", body["error"]
  end

  test "POST /bug-reports rejects invalid severity with 422" do
    post_intake(metadata: valid_metadata("severity" => "urgent"))
    assert_response :unprocessable_entity
    body = JSON.parse(response.body)
    assert_equal "schema_error", body["error"]
  end

  test "POST /bug-reports rejects non-PNG body with 415" do
    post_intake(screenshot_bytes: "JFIF junk".b)
    assert_response :unsupported_media_type
    body = JSON.parse(response.body)
    assert_equal "unsupported_media_type", body["error"]
  end

  test "POST /bug-reports enforces upload cap with 413 + limit_bytes" do
    BugFab.configuration.max_upload_mb = 0 # 0 MiB → any non-empty PNG is too large
    post_intake
    assert_response :payload_too_large
    body = JSON.parse(response.body)
    assert_equal "payload_too_large", body["error"]
    assert body["limit_bytes"].is_a?(Integer)
  ensure
    BugFab.configuration.max_upload_mb = BugFab::Configuration::DEFAULT_MAX_UPLOAD_MB
  end

  test "GET /reports returns paginated JSON list with stats" do
    post_intake
    post_intake
    get "/bug-fab/reports"
    assert_response :ok
    body = JSON.parse(response.body)
    assert_equal 2, body["total"]
    assert_equal 1, body["page"]
    assert_kind_of Array, body["items"]
    assert_kind_of Hash,  body["stats"]
    assert_equal 2, body["stats"]["open"]
  end

  test "GET /reports/:id returns full detail with snake_case keys" do
    post_intake
    id = BugFab::BugReport.first.id
    get "/bug-fab/reports/#{id}"
    assert_response :ok
    body = JSON.parse(response.body)
    %w[id title report_type severity status created_at server_user_agent client_reported_user_agent
       protocol_version lifecycle context reporter tags].each do |k|
      assert body.key?(k), "missing key #{k}"
    end
  end

  test "GET /reports/:id/screenshot returns image/png bytes" do
    post_intake
    id = BugFab::BugReport.first.id
    get "/bug-fab/reports/#{id}/screenshot"
    assert_response :ok
    assert_equal "image/png", response.media_type
  end

  test "PUT /reports/:id/status updates and appends lifecycle" do
    post_intake
    id = BugFab::BugReport.first.id
    put "/bug-fab/reports/#{id}/status",
        params: { status: "fixed", fix_commit: "abc", fix_description: "fixed it" }.to_json,
        headers: { "Content-Type" => "application/json" }
    assert_response :ok
    body = JSON.parse(response.body)
    assert_equal "fixed", body["status"]
    assert_equal 2, body["lifecycle"].size
  end

  test "PUT /reports/:id/status rejects bad status with 422" do
    post_intake
    id = BugFab::BugReport.first.id
    put "/bug-fab/reports/#{id}/status",
        params: { status: "urgent" }.to_json,
        headers: { "Content-Type" => "application/json" }
    assert_response :unprocessable_entity
    body = JSON.parse(response.body)
    assert_equal "schema_error", body["error"]
  end

  test "DELETE /reports/:id returns 204 and removes the record" do
    post_intake
    id = BugFab::BugReport.first.id
    delete "/bug-fab/reports/#{id}"
    assert_response :no_content
    assert_nil BugFab::BugReport.find_by(id: id)
  end

  test "viewer permission gating returns 403 with envelope" do
    post_intake
    id = BugFab::BugReport.first.id
    BugFab.configuration.viewer_permissions[:can_delete] = false
    delete "/bug-fab/reports/#{id}"
    assert_response :forbidden
    body = JSON.parse(response.body)
    assert_equal "forbidden", body["error"]
  ensure
    BugFab.configuration.viewer_permissions[:can_delete] = true
  end
end
