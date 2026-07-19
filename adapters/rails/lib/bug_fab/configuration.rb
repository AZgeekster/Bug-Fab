# frozen_string_literal: true

require "pathname"

module BugFab
  # Centralized configuration for the engine.
  #
  # All knobs are set in `config/initializers/bug_fab.rb` (created by the
  # `bug_fab:install` generator):
  #
  #     BugFab.configure do |config|
  #       config.storage_root = Rails.root.join("storage", "bug-fab")
  #       config.max_upload_mb = 10
  #       config.viewer_page_size = 20
  #       config.viewer_permissions = {
  #         can_edit_status: true,
  #         can_delete: true,
  #         can_bulk: true
  #       }
  #       config.id_prefix = ENV["BUG_FAB_ID_PREFIX"] # e.g., "P" or "D"
  #
  #       config.github_enabled = false
  #       config.github_pat     = ENV["BUG_FAB_GITHUB_PAT"]
  #       config.github_repo    = ENV["BUG_FAB_GITHUB_REPO"]
  #       config.github_api_base = "https://api.github.com"
  #     end
  class Configuration
    # Default screenshot upload cap, in MiB. Mirrors the Python reference.
    DEFAULT_MAX_UPLOAD_MB = 10

    # Default viewer page size when the request omits `page_size`.
    DEFAULT_PAGE_SIZE = 20

    # Where to persist screenshot bytes on disk. Bug-Fab's hard rule:
    # screenshots live on disk, NEVER as DB BLOBs / Active Storage blobs.
    # This keeps storage cheap, exports easy, and avoids database bloat.
    attr_accessor :storage_root

    # Maximum allowed screenshot size in MiB. Enforced after the multipart
    # body is fully read; payloads above this cap return `413 payload_too_large`.
    attr_accessor :max_upload_mb

    # Default page size for `GET /reports`. Capped at 200 even when the
    # request asks for more.
    attr_accessor :viewer_page_size

    # Per-route gates. Each key disables a viewer-side mutation when
    # falsy. Mount-point auth still wraps the routes — this is an
    # additional in-band veto for read-only deployments.
    attr_accessor :viewer_permissions

    # Optional environment-aware ID prefix. When set, intake assigns
    # `bug-{prefix}NNN` IDs (e.g., `bug-P038`, `bug-D012`) so multi-env
    # deployments writing to a shared collector remain distinguishable.
    attr_accessor :id_prefix

    # GitHub Issues sync toggles. When enabled, intake POSTs an issue per
    # report; status updates close / reopen the linked issue. Failures are
    # logged via Rails.logger.warn and never raised.
    attr_accessor :github_enabled, :github_pat, :github_repo, :github_api_base

    # Optional callable returning the actor identifier ("by" field) for
    # lifecycle log entries. Receives the controller request and returns
    # a string or nil. Defaults to "viewer" when nil.
    attr_accessor :actor_resolver

    def initialize
      @storage_root        = nil # Set by initializer or falls back at runtime.
      @max_upload_mb       = DEFAULT_MAX_UPLOAD_MB
      @viewer_page_size    = DEFAULT_PAGE_SIZE
      @viewer_permissions  = {
        can_edit_status: true,
        can_delete: true,
        can_bulk: true
      }
      @id_prefix           = nil
      @github_enabled      = false
      @github_pat          = nil
      @github_repo         = nil
      @github_api_base     = "https://api.github.com"
      @actor_resolver      = nil
    end

    # Resolve the screenshot storage root. Falls back to
    # `Rails.root/storage/bug-fab` when nothing is configured so first-boot
    # without an initializer still works.
    def resolved_storage_root
      root = @storage_root || (defined?(Rails) ? Rails.root.join("storage", "bug-fab") : Pathname.pwd.join("storage", "bug-fab"))
      Pathname.new(root)
    end

    # Maximum upload bytes (the inverse of `max_upload_mb`).
    def max_upload_bytes
      @max_upload_mb * 1024 * 1024
    end

    def github_configured?
      @github_enabled && @github_pat.to_s != "" && @github_repo.to_s != ""
    end
  end
end
