# frozen_string_literal: true

module BugFab
  # Helpers for emitting the Bug-Fab v0.1 protocol error envelope:
  #
  #     { "error": "<machine-readable code>", "detail": "<message-or-list>" }
  #
  # Every non-2xx response (except 204 and the binary 415s where we still
  # keep a JSON body for diagnostics) MUST use this shape per
  # `repo/docs/PROTOCOL.md` § "Error response shape".
  module Errors
    # Internal exception type carrying both the HTTP status and the
    # protocol error code. Controllers rescue this and render the envelope.
    class ProtocolError < StandardError
      attr_reader :status, :code, :detail, :extra

      def initialize(status:, code:, detail:, extra: {})
        super("#{code}: #{detail}")
        @status = status
        @code   = code
        @detail = detail
        @extra  = extra
      end

      # Render-ready hash matching the protocol's error envelope.
      def to_envelope
        { error: code, detail: detail }.merge(extra)
      end
    end

    module_function

    # Raise a 400 with `validation_error`. Used for missing multipart parts
    # or unparseable metadata JSON.
    def validation_error!(detail)
      raise ProtocolError.new(status: 400, code: "validation_error", detail: detail)
    end

    # Raise a 400 with `unsupported_protocol_version`.
    def unsupported_protocol_version!(submitted)
      raise ProtocolError.new(
        status: 400,
        code: "unsupported_protocol_version",
        detail: "Submitted protocol_version #{submitted.inspect} is not supported by this adapter " \
                "(expected #{BugFab::PROTOCOL_VERSION.inspect})"
      )
    end

    # Raise a 413 with `payload_too_large`. The protocol mandates `limit_bytes`
    # in the body so clients know the cap.
    def payload_too_large!(limit_bytes:, detail: "Screenshot exceeds maximum size")
      raise ProtocolError.new(
        status: 413,
        code: "payload_too_large",
        detail: detail,
        extra: { limit_bytes: limit_bytes }
      )
    end

    # Raise a 415 with `unsupported_media_type`. Used when the screenshot
    # bytes fail the PNG magic-byte check.
    def unsupported_media_type!(detail = "Screenshot must be a PNG image (image/png)")
      raise ProtocolError.new(status: 415, code: "unsupported_media_type", detail: detail)
    end

    # Raise a 422 with `schema_error`. Used when metadata parses as JSON
    # but fails validation (invalid enum value, missing required field, etc.).
    def schema_error!(detail)
      raise ProtocolError.new(status: 422, code: "schema_error", detail: detail)
    end

    # Raise a 503 with `storage_unavailable`. Used when the configured
    # storage root cannot be reached.
    def storage_unavailable!(detail)
      raise ProtocolError.new(status: 503, code: "storage_unavailable", detail: detail)
    end
  end
end
