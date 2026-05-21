# frozen_string_literal: true

module BugFab
  # Bulk operations: post-release cleanup endpoints. Both gated by the
  # `:can_bulk` viewer permission so consumers running a read-only
  # deployment can leave the routes mounted but disabled.
  class BulkActionsController < ApplicationController
    before_action :guard_can_bulk

    # `POST /bulk-close-fixed`
    def close_fixed
      closed = BugReport.bulk_close_fixed!(by: bug_fab_actor)
      render json: { closed: closed }, status: 200
    end

    # `POST /bulk-archive-closed`
    def archive_closed
      archived = BugReport.bulk_archive_closed!
      render json: { archived: archived }, status: 200
    end

    private

    def guard_can_bulk
      require_permission!(:can_bulk)
    end
  end
end
