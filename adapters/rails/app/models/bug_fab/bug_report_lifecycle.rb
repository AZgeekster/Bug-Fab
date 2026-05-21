# frozen_string_literal: true

module BugFab
  # Append-only audit-log row attached to a {BugReport}. One entry per
  # state-changing action: created / status_changed / deleted / archived.
  #
  # Per PROTOCOL.md, field names lock to `action / by / at` — drift to
  # `status / changed_by / timestamp` is the cautionary tale that
  # motivated the lock.
  class BugReportLifecycle < ApplicationRecord
    self.table_name = "bug_fab_lifecycle_events"

    belongs_to :bug_report,
               class_name: "BugFab::BugReport",
               foreign_key: :bug_report_id,
               inverse_of: :lifecycle_events

    # Render this row as the protocol's LifecycleEvent shape.
    def to_protocol_hash
      {
        action: action,
        by: by.to_s,
        at: at.iso8601,
        fix_commit: fix_commit.to_s,
        fix_description: fix_description.to_s
      }
    end
  end
end
