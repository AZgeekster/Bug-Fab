# frozen_string_literal: true

require "json"

module BugFab
  # Pure-Ruby validators that mirror the authoritative schema at
  # `repo/docs/protocol-schema.json`.
  #
  # These are intentionally hand-written instead of using `dry-schema` or
  # `JSON::Schema` so the gem's runtime dependency surface stays at "Rails"
  # and nothing else. Trade-off: every protocol bump touches this file.
  module Validation
    PROTOCOL_VERSION = BugFab::PROTOCOL_VERSION

    # Locked enum vocabularies. Adapters MUST reject other values on WRITE
    # paths with 422; READ paths are permissive (deprecated values still
    # parse, see `repo/docs/PROTOCOL.md` § "Deprecated-values rule").
    SEVERITIES    = %w[low medium high critical].freeze
    STATUSES      = %w[open investigating fixed closed].freeze
    REPORT_TYPES  = %w[bug feature_request].freeze

    # Length caps from the schema.
    TITLE_MAX     = 200
    REPORTER_MAX  = 256

    # PNG magic-byte signature. Per protocol v0.1 the only accepted
    # screenshot media type is image/png; anything else is 415.
    PNG_SIGNATURE = "\x89PNG\r\n\x1a\n".b.freeze

    module_function

    # Parse the multipart `metadata` form value. Returns a hash on success,
    # raises {Errors::ProtocolError} on any failure.
    def parse_metadata(raw)
      Errors.validation_error!("metadata part is required") if raw.nil? || raw.to_s.empty?

      begin
        parsed = JSON.parse(raw)
      rescue JSON::ParserError => e
        Errors.validation_error!("metadata is not valid JSON: #{e.message}")
      end

      Errors.validation_error!("metadata must be a JSON object") unless parsed.is_a?(Hash)
      parsed
    end

    # Validate the parsed metadata hash against the v0.1 wire protocol.
    # Mutates nothing; returns the same hash with defaults filled in.
    # Raises {Errors::ProtocolError} on any constraint violation.
    def validate_create!(metadata)
      version = metadata["protocol_version"]
      Errors.unsupported_protocol_version!(version) if version != PROTOCOL_VERSION

      title = metadata["title"]
      unless title.is_a?(String) && !title.empty? && title.length <= TITLE_MAX
        Errors.schema_error!("title is required and must be a string of 1..#{TITLE_MAX} characters")
      end

      client_ts = metadata["client_ts"]
      unless client_ts.is_a?(String) && !client_ts.empty?
        Errors.schema_error!("client_ts is required and must be a non-empty ISO 8601 string")
      end

      report_type = metadata["report_type"] ||= "bug"
      unless REPORT_TYPES.include?(report_type)
        Errors.schema_error!("report_type must be one of: #{REPORT_TYPES.join(', ')}")
      end

      severity = metadata["severity"] ||= "medium"
      unless SEVERITIES.include?(severity)
        Errors.schema_error!("severity must be one of: #{SEVERITIES.join(', ')}")
      end

      tags = metadata["tags"] ||= []
      unless tags.is_a?(Array) && tags.all? { |t| t.is_a?(String) }
        Errors.schema_error!("tags must be an array of strings")
      end

      reporter = metadata["reporter"] ||= {}
      Errors.schema_error!("reporter must be an object") unless reporter.is_a?(Hash)
      %w[name email user_id].each do |field|
        value = reporter[field]
        next if value.nil?
        unless value.is_a?(String) && value.length <= REPORTER_MAX
          Errors.schema_error!("reporter.#{field} must be a string with length <= #{REPORTER_MAX}")
        end
      end

      context = metadata["context"] ||= {}
      Errors.schema_error!("context must be an object") unless context.is_a?(Hash)

      # Fill in context defaults that the round-trip detail response expects.
      context["url"]              ||= ""
      context["module"]           ||= ""
      context["user_agent"]       ||= ""
      context["viewport_width"]   ||= 0
      context["viewport_height"]  ||= 0
      context["console_errors"]   ||= []
      context["network_log"]      ||= []
      context["source_mapping"]   ||= {}
      context["app_version"]      ||= ""
      context["environment"]      ||= ""

      metadata["description"]       ||= ""
      metadata["expected_behavior"] ||= ""

      metadata
    end

    # Validate a `PUT /reports/{id}/status` body. Returns a normalized hash
    # on success; raises on bad input.
    def validate_status_update!(payload)
      Errors.schema_error!("body must be a JSON object") unless payload.is_a?(Hash)

      status_value = payload["status"]
      unless STATUSES.include?(status_value)
        Errors.schema_error!("status must be one of: #{STATUSES.join(', ')}")
      end

      {
        "status" => status_value,
        "fix_commit" => payload["fix_commit"].to_s,
        "fix_description" => payload["fix_description"].to_s
      }
    end

    # Magic-byte PNG check. Returns true iff `bytes` begins with the
    # 8-byte PNG signature. Anything else is 415.
    def png?(bytes)
      bytes.is_a?(String) && bytes.byteslice(0, 8) == PNG_SIGNATURE
    end
  end
end
