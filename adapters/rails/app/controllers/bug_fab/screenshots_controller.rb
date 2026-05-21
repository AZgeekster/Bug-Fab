# frozen_string_literal: true

module BugFab
  # `GET /reports/:id/screenshot` — raw PNG bytes.
  #
  # The protocol locks this response to `image/png`. PNG was the only
  # accepted intake format (magic-byte-checked at submit time), so the
  # bytes on disk are always PNG.
  class ScreenshotsController < ApplicationController
    def show
      report = BugReport.find_by!(id: params[:id])
      path = report.screenshot_path.to_s

      unless path.present? && File.exist?(path)
        raise ActiveRecord::RecordNotFound, "screenshot file missing"
      end

      send_file path, type: "image/png", disposition: "inline"
    end
  end
end
