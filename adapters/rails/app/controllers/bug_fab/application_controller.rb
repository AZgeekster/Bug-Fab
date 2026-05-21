# frozen_string_literal: true

module BugFab
  # Base controller for every Bug-Fab endpoint. Handles three cross-cutting
  # concerns:
  #
  # 1. CSRF: the JS bundle calls intake + viewer mutations from a different
  #    origin context than a Rails-rendered form, so CSRF is satisfied via
  #    `protect_from_forgery with: :null_session`. Consumers wanting CSRF
  #    tokens on Bug-Fab routes should re-enable on a per-route basis in
  #    a host-side override.
  #
  # 2. Protocol error envelope: rescues {Errors::ProtocolError} and renders
  #    the documented JSON shape. Generic exceptions render as 500 with
  #    `internal_error` so consumer logs show the failure but the response
  #    surface stays protocol-conformant.
  #
  # 3. Permission gating: provides {#require_permission!} which routes can
  #    invoke against `BugFab.configuration.viewer_permissions` to gate
  #    destructive endpoints.
  class ApplicationController < ActionController::Base
    protect_from_forgery with: :null_session

    rescue_from BugFab::Errors::ProtocolError, with: :render_protocol_error
    rescue_from ActiveRecord::RecordNotFound, with: :render_not_found
    rescue_from StandardError, with: :render_internal_error if Rails.env.production?

    private

    # Resolve the actor identifier used in lifecycle audit entries. Calls
    # the optional configured `actor_resolver` lambda; falls back to the
    # opaque `"viewer"` sentinel.
    def bug_fab_actor
      resolver = BugFab.configuration.actor_resolver
      value = resolver&.call(request)
      value.to_s.presence || "viewer"
    end

    # Helper used by viewer-side mutation endpoints. Raises 403 with the
    # protocol envelope when the named permission flag is disabled.
    def require_permission!(flag)
      return if BugFab.configuration.viewer_permissions[flag]

      raise BugFab::Errors::ProtocolError.new(
        status: 403,
        code: "forbidden",
        detail: "viewer action '#{flag}' is disabled by configuration"
      )
    end

    def render_protocol_error(exception)
      render json: exception.to_envelope, status: exception.status
    end

    def render_not_found(_exception)
      render json: { error: "not_found", detail: "Bug report not found" }, status: 404
    end

    def render_internal_error(exception)
      Rails.logger.error("[bug_fab] #{exception.class}: #{exception.message}")
      render json: { error: "internal_error", detail: "An unexpected error occurred" }, status: 500
    end
  end
end
