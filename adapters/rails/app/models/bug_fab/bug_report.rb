# frozen_string_literal: true

require "json"
require "fileutils"
require "securerandom"
require "time"

module BugFab
  # The main bug-report record. Mirrors `bug_fab/storage/_models.py:BugReportORM`
  # in the Python reference: typed columns hold the indexable subset, the
  # full original wire payload lives verbatim in `metadata_json` for
  # round-trip fidelity (forward-additive).
  #
  # Screenshots are NEVER stored in the database. The `screenshot_path`
  # column points to a file on disk under `BugFab.configuration.resolved_storage_root`.
  class BugReport < ApplicationRecord
    self.table_name = "bug_fab_bug_reports"

    # Locked enums for write-time validation. Read-side queries are
    # permissive — deprecated values stored on older rows still display.
    SEVERITIES = Validation::SEVERITIES
    STATUSES   = Validation::STATUSES

    has_many :lifecycle_events,
             -> { order(:at) },
             class_name: "BugFab::BugReportLifecycle",
             foreign_key: :bug_report_id,
             dependent: :destroy,
             inverse_of: :bug_report

    # Filter scopes used by the list endpoint.
    scope :not_archived, -> { where(archived_at: nil) }
    scope :archived,     -> { where.not(archived_at: nil) }
    scope :with_status,      ->(value) { value.blank? ? all : where(status: value) }
    scope :with_severity,    ->(value) { value.blank? ? all : where(severity: value) }
    scope :with_environment, ->(value) { value.blank? ? all : where(environment: value) }
    scope :with_module,      ->(value) { value.blank? ? all : where(module_name: value) }

    # Persist a new report from the validated metadata hash plus screenshot
    # bytes. Returns the saved record. Raises on storage errors.
    #
    # `request_user_agent` is the trusted server-captured User-Agent header;
    # `client_user_agent` is the diagnostic value the client sent in
    # `context.user_agent`. Both are surfaced on the detail response so the
    # viewer can compare them.
    def self.create_from_payload!(metadata, screenshot_bytes, request_user_agent:)
      id = next_id
      path = persist_screenshot(id, screenshot_bytes)
      now  = Time.now.utc
      context = metadata["context"] || {}
      reporter = metadata["reporter"] || {}

      # Stuff dual-UA + environment back into the payload so the stored
      # JSON is round-trip-complete with everything the detail response
      # surfaces at the top level.
      stored_metadata = metadata.dup
      stored_metadata["server_user_agent"]         = request_user_agent.to_s
      stored_metadata["client_reported_user_agent"] = context["user_agent"].to_s
      stored_metadata["environment"]               = context["environment"].to_s

      record = create!(
        id: id,
        protocol_version: metadata["protocol_version"],
        title: metadata["title"],
        description: metadata["description"].to_s,
        report_type: metadata["report_type"] || "bug",
        severity: metadata["severity"] || "medium",
        status: "open",
        environment: context["environment"].to_s,
        module_name: context["module"].to_s,
        page_url: context["url"].to_s,
        app_version: context["app_version"].to_s,
        reporter_email: reporter["email"].to_s,
        reporter_name: reporter["name"].to_s,
        reporter_user_id: reporter["user_id"].to_s,
        client_ts: metadata["client_ts"].to_s,
        server_user_agent: request_user_agent.to_s,
        client_reported_user_agent: context["user_agent"].to_s,
        screenshot_path: path.to_s,
        metadata_json: stored_metadata.to_json,
        received_at: now,
        updated_at_protocol: now
      )

      record.lifecycle_events.create!(
        action: "created",
        by: "anonymous",
        at: now,
        fix_commit: "",
        fix_description: ""
      )

      record
    end

    # Update lifecycle status. Appends a `status_changed` event. Returns
    # the reloaded record on success.
    def apply_status_update!(status:, fix_commit:, fix_description:, by:)
      raise ArgumentError, "invalid status: #{status}" unless STATUSES.include?(status)

      transaction do
        update!(status: status, updated_at_protocol: Time.now.utc)
        lifecycle_events.create!(
          action: "status_changed",
          by: by.to_s,
          at: Time.now.utc,
          fix_commit: fix_commit.to_s,
          fix_description: fix_description.to_s
        )
      end
      reload
    end

    # Bulk transition: every `fixed` record → `closed`. Returns count.
    def self.bulk_close_fixed!(by:)
      now = Time.now.utc
      ids = where(status: "fixed").pluck(:id)
      return 0 if ids.empty?

      transaction do
        where(id: ids).update_all(status: "closed", updated_at_protocol: now)
        ids.each do |id|
          BugReportLifecycle.create!(
            bug_report_id: id,
            action: "status_changed",
            by: by.to_s,
            at: now,
            fix_commit: "",
            fix_description: ""
          )
        end
      end
      ids.size
    end

    # Bulk archive: every `closed` record gets an archived_at timestamp.
    # Per protocol, archived reports are excluded from list responses
    # unless `include_archived=true`.
    def self.bulk_archive_closed!
      now = Time.now.utc
      ids = where(status: "closed", archived_at: nil).pluck(:id)
      return 0 if ids.empty?

      transaction do
        where(id: ids).update_all(archived_at: now)
        ids.each do |id|
          BugReportLifecycle.create!(
            bug_report_id: id,
            action: "archived",
            by: "system",
            at: now,
            fix_commit: "",
            fix_description: ""
          )
        end
      end
      ids.size
    end

    # Hard delete: removes the row, cascades to lifecycle events, and
    # unlinks the screenshot file (best effort).
    def self.hard_delete!(id)
      record = find_by(id: id)
      return false unless record

      path = record.screenshot_path
      record.destroy!
      File.delete(path) if path && File.exist?(path)
      true
    end

    # ID-counter helper. Stored as a single-row table (`bug_fab_id_counter`)
    # to stay portable across SQLite / Postgres / MySQL without depending
    # on dialect-specific sequences.
    def self.next_id
      transaction do
        counter = BugReportIdCounter.lock.find_or_create_by!(id: 1) { |c| c.last_value = 0 }
        counter.update!(last_value: counter.last_value + 1)
        prefix = BugFab.configuration.id_prefix.to_s
        # bug-NNN with optional single-letter prefix per PROTOCOL.md regex:
        # `^bug-[A-Za-z]?\d{3,}$`
        prefix_part = prefix.empty? ? "" : prefix[0]
        format("bug-%s%03d", prefix_part, counter.last_value)
      end
    end

    def self.persist_screenshot(id, bytes)
      root = BugFab.configuration.resolved_storage_root
      FileUtils.mkdir_p(root)
      path = root.join("#{id}.png")
      File.binwrite(path, bytes)
      path
    rescue Errno::EACCES, Errno::ENOENT, Errno::EROFS => e
      Errors.storage_unavailable!("storage root is unwritable: #{e.message}")
    end

    # Return the dehydrated metadata hash stored under `metadata_json`,
    # falling back to {} on parse failure.
    def stored_metadata
      JSON.parse(metadata_json || "{}")
    rescue JSON::ParserError
      {}
    end

    # Build the protocol's BugReportSummary shape.
    def to_summary
      {
        id: id,
        title: title,
        report_type: report_type || "bug",
        severity: severity || "medium",
        status: status || "open",
        module: module_name.to_s,
        created_at: received_at.iso8601,
        has_screenshot: screenshot_path.present? && File.exist?(screenshot_path.to_s),
        github_issue_url: github_issue_url
      }
    end

    # Build the protocol's BugReportDetail shape — round-trips the original
    # metadata, augmented with the lifecycle log and dual-UA top-level fields.
    def to_detail
      meta = stored_metadata
      context = meta["context"] || {}
      reporter = meta["reporter"] || {}

      {
        id: id,
        title: title,
        report_type: report_type || "bug",
        severity: severity || "medium",
        status: status || "open",
        module: module_name.to_s,
        created_at: received_at.iso8601,
        has_screenshot: screenshot_path.present? && File.exist?(screenshot_path.to_s),
        github_issue_url: github_issue_url,
        description: description.to_s,
        expected_behavior: meta["expected_behavior"].to_s,
        tags: meta["tags"] || [],
        reporter: {
          name: reporter["name"].to_s,
          email: reporter["email"].to_s,
          user_id: reporter["user_id"].to_s
        },
        context: context,
        lifecycle: lifecycle_events.map(&:to_protocol_hash),
        server_user_agent: server_user_agent.to_s,
        client_reported_user_agent: client_reported_user_agent.to_s,
        environment: environment.to_s,
        client_ts: client_ts.to_s,
        protocol_version: protocol_version.to_s,
        updated_at: (updated_at_protocol || received_at).iso8601,
        github_issue_number: github_issue_number
      }
    end
  end

  # Single-row counter table. Used to mint sequential `bug-NNN` IDs in a
  # transaction without depending on database-specific sequence support.
  class BugReportIdCounter < ApplicationRecord
    self.table_name = "bug_fab_id_counter"
  end
end
