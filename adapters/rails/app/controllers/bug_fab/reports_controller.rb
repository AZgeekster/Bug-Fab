# frozen_string_literal: true

require "json"

module BugFab
  # The bulk of the Bug-Fab v0.1 wire protocol. Six of the eight endpoints
  # live here; the other two (screenshot serve, bulk operations) live in
  # dedicated controllers for clarity.
  #
  # All JSON responses use snake_case keys. Rails' default `to_json` does
  # NOT mutate key case (that's the realm of ActiveModelSerializers etc.),
  # so the keys we render verbatim ARE the keys clients see.
  class ReportsController < ApplicationController
    # `POST /bug-reports` — intake. Multipart body, JSON response.
    def create
      metadata_raw = params[:metadata]
      screenshot   = params[:screenshot]

      Errors.validation_error!("metadata part is required") if metadata_raw.blank?
      Errors.validation_error!("screenshot part is required") if screenshot.blank?

      metadata = Validation.parse_metadata(metadata_raw.to_s)
      Validation.validate_create!(metadata)

      bytes = read_screenshot_bytes(screenshot)
      Errors.validation_error!("Screenshot file is empty") if bytes.empty?

      cap = BugFab.configuration.max_upload_bytes
      if bytes.bytesize > cap
        Errors.payload_too_large!(
          limit_bytes: cap,
          detail: "Screenshot exceeds maximum size of #{BugFab.configuration.max_upload_mb} MiB"
        )
      end

      Errors.unsupported_media_type! unless Validation.png?(bytes)

      report = BugReport.create_from_payload!(
        metadata,
        bytes,
        request_user_agent: request.user_agent.to_s
      )

      issue_number, issue_url = GitHub.create_issue(report.to_detail)
      if issue_number && issue_url
        report.update!(github_issue_number: issue_number, github_issue_url: issue_url)
      end

      render json: {
        id: report.id,
        received_at: report.received_at.iso8601,
        stored_at: "bug-fab://reports/#{report.id}",
        github_issue_url: issue_url
      }, status: 201
    end

    # `GET /` — HTML list. Mounted at the engine root so consumers visiting
    # `/bug-fab` (or wherever they mounted) see the viewer index.
    def index
      page      = (params[:page] || 1).to_i.clamp(1, 100_000)
      page_size = (params[:page_size] || BugFab.configuration.viewer_page_size).to_i.clamp(1, 200)

      @scope = filtered_scope
      @total = @scope.count
      @items = @scope.order(received_at: :desc).limit(page_size).offset((page - 1) * page_size)
      @stats = compute_stats(filtered_scope_unscoped_status)
      @page = page
      @page_size = page_size
      @total_pages = ((@total + page_size - 1) / page_size).clamp(1, 100_000)
      @permissions = BugFab.configuration.viewer_permissions
      @filters = {
        status: params[:status].to_s,
        severity: params[:severity].to_s,
        module: params[:module].to_s,
        environment: params[:environment].to_s
      }

      render "bug_fab/reports/index", layout: "bug_fab"
    end

    # `GET /reports` — JSON list. Same filters as the HTML index.
    def list_json
      page      = (params[:page] || 1).to_i.clamp(1, 100_000)
      page_size = (params[:page_size] || BugFab.configuration.viewer_page_size).to_i.clamp(1, 200)

      scope = filtered_scope
      total = scope.count
      items = scope.order(received_at: :desc).limit(page_size).offset((page - 1) * page_size).map(&:to_summary)
      stats = compute_stats(filtered_scope_unscoped_status)

      render json: {
        items: items,
        total: total,
        page: page,
        page_size: page_size,
        stats: stats
      }, status: 200
    end

    # `GET /:id` — HTML detail page (mount-relative path).
    def show
      @report = BugReport.find_by!(id: params[:id])
      @permissions = BugFab.configuration.viewer_permissions
      render "bug_fab/reports/show", layout: "bug_fab"
    end

    # `GET /reports/:id` — JSON detail.
    def show_json
      report = BugReport.find_by!(id: params[:id])
      render json: report.to_detail, status: 200
    end

    # `PUT /reports/:id/status`
    def update_status
      require_permission!(:can_edit_status)
      report = BugReport.find_by!(id: params[:id])

      payload = JSON.parse(request.body.read.presence || "{}")
      normalized = Validation.validate_status_update!(payload)

      report.apply_status_update!(
        status: normalized["status"],
        fix_commit: normalized["fix_commit"],
        fix_description: normalized["fix_description"],
        by: bug_fab_actor
      )

      GitHub.sync_state(report.github_issue_number, normalized["status"]) if report.github_issue_number

      render json: report.to_detail, status: 200
    rescue JSON::ParserError => e
      Errors.validation_error!("body is not valid JSON: #{e.message}")
    end

    # `DELETE /reports/:id`
    def destroy
      require_permission!(:can_delete)
      deleted = BugReport.hard_delete!(params[:id])
      raise ActiveRecord::RecordNotFound unless deleted

      head :no_content
    end

    private

    def filtered_scope
      scope = BugReport.all
      scope = scope.not_archived unless ActiveModel::Type::Boolean.new.cast(params[:include_archived])
      scope = scope.with_status(params[:status])
      scope = scope.with_severity(params[:severity])
      scope = scope.with_module(params[:module])
      scope = scope.with_environment(params[:environment])
      scope
    end

    # Stats reflect the filtered set IGNORING the status filter — the UI
    # uses these counts to drive the status-tab buttons, which would zero
    # out if we re-applied the active status filter to themselves.
    def filtered_scope_unscoped_status
      scope = BugReport.all
      scope = scope.not_archived unless ActiveModel::Type::Boolean.new.cast(params[:include_archived])
      scope = scope.with_severity(params[:severity])
      scope = scope.with_module(params[:module])
      scope = scope.with_environment(params[:environment])
      scope
    end

    def compute_stats(scope)
      counts = scope.group(:status).count
      Validation::STATUSES.each_with_object({}) { |status, h| h[status] = counts[status].to_i }
    end

    # Read screenshot bytes from either an ActionDispatch::Http::UploadedFile
    # or a plain IO (rack-test sometimes passes one directly).
    def read_screenshot_bytes(part)
      if part.respond_to?(:read)
        part.tap(&:rewind).read.to_s.b
      else
        part.to_s.b
      end
    end
  end
end
