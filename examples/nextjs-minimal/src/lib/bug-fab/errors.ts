// Error envelope factories for the Bug-Fab wire protocol.
//
// Per PROTOCOL.md § Error response shape, every non-2xx response (except
// 204 and the binary-PNG 404) returns:
//     { error: <machine_code>, detail: <string | array> }
// Some error codes attach extra fields (e.g., `limit_bytes` on 413,
// `retry_after_seconds` on 429). The factories below produce ready-to-
// serialize envelopes; the Route Handler wraps them in NextResponse.json
// with the appropriate HTTP status.

import type { BugFabError } from './types'

export const Errors = {
  /** 400 — multipart parts missing, JSON malformed, required fields missing. */
  validationError(detail: string | unknown[]): BugFabError {
    return { error: 'validation_error', detail }
  },

  /** 400 — submitted protocol_version is not understood by this adapter. */
  unsupportedProtocolVersion(submitted: string): BugFabError {
    return {
      error: 'unsupported_protocol_version',
      detail: `protocol_version "${submitted}" is not supported by this adapter`,
    }
  },

  /** 413 — screenshot or metadata exceeds the configured cap. */
  payloadTooLarge(limitBytes: number): BugFabError {
    return {
      error: 'payload_too_large',
      detail: `payload exceeds the configured limit of ${limitBytes} bytes`,
      limit_bytes: limitBytes,
    }
  },

  /** 415 — screenshot is not a PNG (or multipart Content-Type is wrong). */
  unsupportedMediaType(detail = 'screenshot must be a PNG image'): BugFabError {
    return { error: 'unsupported_media_type', detail }
  },

  /** 422 — metadata parses but fails validation (bad enum, wrong type, etc.). */
  schemaError(detail: string | unknown[]): BugFabError {
    return { error: 'schema_error', detail }
  },

  /** 404 — report id unknown. Returned as JSON for non-binary endpoints. */
  notFound(reportId: string): BugFabError {
    return {
      error: 'validation_error',
      detail: `bug report ${reportId} not found`,
    }
  },

  /** 500 — unhandled server exception. Detail is intentionally generic. */
  internalError(detail = 'internal server error'): BugFabError {
    return { error: 'internal_error', detail }
  },
} as const
