# frozen_string_literal: true

require "test_helper"

# Regression tests for BugFab::GitHub's read of the detail hash.
#
# `BugReport#to_detail` returns SYMBOL keys at the top level. `github.rb` read
# STRING keys throughout (`detail['title']`, `detail.dig('context', 'module')`),
# so every interpolation resolved to nil: issues arrived titled "[Bug-Fab] "
# with an empty body and only the generic `bug-fab` label. The POST returned
# 201, so nothing surfaced the failure.
#
# The suite sets `github_enabled = false` (test_helper.rb), which is exactly
# why this shipped broken -- no test ever built an issue payload. These tests
# call the formatters directly, against a real `to_detail`, so they cannot
# drift from the hash they consume.
class BugFab::GitHubTest < ActiveSupport::TestCase
  include BugFabTestHelpers

  setup do
    BugFab::BugReportLifecycle.delete_all
    BugFab::BugReport.delete_all
    BugFab::BugReportIdCounter.delete_all

    @report = BugFab::BugReport.create_from_payload!(
      valid_metadata,
      fake_png_bytes,
      request_user_agent: "test"
    )
    @detail = @report.to_detail
  end

  test "issue title carries the report title" do
    title = "[Bug-Fab] #{@detail[:title]}"
    assert_equal "[Bug-Fab] Save button is unresponsive", title
    refute_equal "[Bug-Fab] ", title
  end

  test "format_body fills every field from the symbol-keyed detail hash" do
    body = BugFab::GitHub.format_body(@detail)

    assert_includes body, "**Severity:** high"
    assert_includes body, "**Module:** checkout"
    assert_includes body, "**Reporter:** alice@example.com"
    assert_includes body, "**App version:** 1.4.2"
    assert_includes body, "**Environment:** prod"
    assert_includes body, "**URL:** https://example.com/cart"
  end

  test "format_body leaves no field blank when the metadata supplies every field" do
    # Every optional field populated, so any blank line in the rendered body is
    # a key-lookup miss rather than genuinely absent data. The pre-fix body was
    # a run of "**Severity:** \n**Module:** \n..." lines.
    full = BugFab::BugReport.create_from_payload!(
      valid_metadata(
        "description" => "Clicking Save does nothing.",
        "expected_behavior" => "The cart persists."
      ),
      fake_png_bytes,
      request_user_agent: "test"
    )
    body = BugFab::GitHub.format_body(full.to_detail)

    body.each_line do |line|
      next unless line.start_with?("**") && line.include?(":**")
      value = line.split(":**", 2).last.strip
      refute_empty value, "field rendered blank: #{line.strip.inspect}"
    end
  end

  test "reporter falls back to name, then anonymous, without picking empty strings" do
    # `to_detail` coerces reporter fields with `.to_s`, so an absent email is
    # "" rather than nil -- a plain `||` would render an empty Reporter line.
    named = @detail.merge(reporter: { name: "Bob", email: "", user_id: "" })
    assert_includes BugFab::GitHub.format_body(named), "**Reporter:** Bob"

    anon = @detail.merge(reporter: { name: "", email: "", user_id: "" })
    assert_includes BugFab::GitHub.format_body(anon), "**Reporter:** anonymous"
  end

  test "build_labels includes the severity and report-type labels" do
    labels = BugFab::GitHub.build_labels(@detail)

    assert_includes labels, "bug-fab"
    assert_includes labels, "severity:high"
    assert_includes labels, "bug"
  end

  test "build_labels omits severity and type when absent rather than emitting blanks" do
    labels = BugFab::GitHub.build_labels(@detail.merge(severity: nil, report_type: nil))

    assert_equal ["bug-fab"], labels
  end

  test "module is read from the detail top level, not from context" do
    # `to_detail` places `module` at the top level; `context` retains STRING
    # keys because it is the raw metadata sub-hash. Reading
    # `detail.dig('context', 'module')` -- as the pre-fix code did -- is nil
    # on both counts.
    assert_equal "checkout", @detail[:module]
    assert_nil @detail.dig(:context, :module)
    assert_equal "checkout", @detail.dig(:context, "module")
  end
end
