# frozen_string_literal: true

require "json"
require "net/http"
require "uri"

module BugFab
  # Best-effort GitHub Issues sync.
  #
  # Per `repo/docs/PROTOCOL.md` § "Failure modes that MUST NOT yield non-2xx",
  # GitHub sync failures during intake or status update MUST be logged and
  # MUST NOT cause the response to be non-2xx. Every method in this module
  # rescues StandardError and returns nil on failure.
  #
  # No background workers (Sidekiq/ActiveJob): per the adapter brief, sync
  # is synchronous best-effort. Consumers wanting async sync can monkey-patch
  # this module to enqueue a job and return immediately, but Bug-Fab itself
  # adds no such dependency.
  module GitHub
    module_function

    # Create an issue from a stored bug-report detail hash. Returns
    # `[issue_number, issue_url]` on success, `[nil, nil]` on any failure.
    def create_issue(detail)
      config = BugFab.configuration
      return [nil, nil] unless config.github_configured?

      uri = URI.join(config.github_api_base + "/", "repos/#{config.github_repo}/issues")
      body = {
        title: "[Bug-Fab] #{detail[:title]}",
        body: format_body(detail),
        labels: build_labels(detail)
      }

      response = post_json(uri, body)
      return [nil, nil] unless response.is_a?(Net::HTTPSuccess)

      parsed = JSON.parse(response.body)
      [parsed["number"], parsed["html_url"]]
    rescue StandardError => e
      logger.warn("[bug_fab] github create_issue failed: #{e.class}: #{e.message}")
      [nil, nil]
    end

    # Mirror a status change onto a linked GitHub issue. fixed/closed → close;
    # open/investigating → reopen. Returns true on success, false otherwise.
    def sync_state(issue_number, status)
      config = BugFab.configuration
      return false unless config.github_configured? && issue_number

      target = case status
               when "fixed", "closed" then "closed"
               when "open", "investigating" then "open"
               else
                 return false
               end

      uri = URI.join(config.github_api_base + "/", "repos/#{config.github_repo}/issues/#{issue_number}")
      response = patch_json(uri, { state: target })
      response.is_a?(Net::HTTPSuccess)
    rescue StandardError => e
      logger.warn("[bug_fab] github sync_state failed: #{e.class}: #{e.message}")
      false
    end

    # Format the issue body. Pure presentation; not authoritative.
    #
    # Key style is load-bearing here. `BugReport#to_detail` returns a hash with
    # SYMBOL keys at the top level, whose `:context` value is the raw metadata
    # sub-hash and therefore has STRING keys. `show.html.erb` already reads it
    # that way (`detail.dig(:context, "url")`).
    #
    # This method used to read string keys throughout (`detail['severity']`,
    # `detail.dig('context', 'module')`), so every interpolation resolved to
    # nil: issues were titled "[Bug-Fab] " with an empty body and only the
    # generic `bug-fab` label. The POST succeeded, so it failed silently. Note
    # `:module` is top-level on the detail hash, not nested under `:context`.
    def format_body(detail)
      context = detail[:context] || {}
      reporter = detail[:reporter] || {}
      # `to_detail` coerces reporter fields with `.to_s`, so absent ones are
      # "" rather than nil -- `||` would happily pick the empty string.
      who = reporter[:email].presence || reporter[:name].presence || "anonymous"

      <<~MD
        **Severity:** #{detail[:severity]}
        **Module:** #{detail[:module]}
        **Reporter:** #{who}
        **App version:** #{context['app_version']}
        **Environment:** #{detail[:environment]}
        **URL:** #{context['url']}

        ---

        #{detail[:description]}

        **Expected:** #{detail[:expected_behavior]}
      MD
    end

    def build_labels(detail)
      labels = ["bug-fab"]
      labels << "severity:#{detail[:severity]}" if detail[:severity].present?
      labels << detail[:report_type] if detail[:report_type].present?
      labels.compact
    end

    def post_json(uri, body)
      request = Net::HTTP::Post.new(uri)
      apply_headers(request)
      request.body = body.to_json
      perform(uri, request)
    end

    def patch_json(uri, body)
      request = Net::HTTP::Patch.new(uri)
      apply_headers(request)
      request.body = body.to_json
      perform(uri, request)
    end

    def apply_headers(request)
      pat = BugFab.configuration.github_pat
      request["Authorization"] = "token #{pat}"
      request["Accept"]        = "application/vnd.github+json"
      request["Content-Type"]  = "application/json"
      request["User-Agent"]    = "bug_fab-rails/#{BugFab::VERSION}"
    end

    def perform(uri, request)
      Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == "https", open_timeout: 5, read_timeout: 10) do |http|
        http.request(request)
      end
    end

    def logger
      defined?(Rails) ? Rails.logger : Logger.new($stdout)
    end
  end
end
