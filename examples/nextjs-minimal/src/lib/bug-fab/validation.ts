// Wire-protocol validation for the intake handler.
//
// Why split out from the Route Handler: keeps the validation policy in
// one place so `severity: "urgent"` (and similar silent-coerce traps)
// can't sneak in through one entry point but not another. Mirrors the
// pydantic model in `bug_fab/schemas.py`.
//
// Validation is intentionally written by hand rather than via a JSON
// Schema runtime: keeps dependencies near-zero and the errors
// human-readable. The protocol-schema.json file at the repo root is the
// authoritative cross-language contract; this module exists to enforce
// it inside Node.

import {
  REPORT_TYPE_VALUES,
  SEVERITY_VALUES,
  STATUS_VALUES,
  SUPPORTED_PROTOCOL_VERSION,
  type BugReportCreate,
  type BugReportStatusUpdate,
} from './types'

/** PNG magic bytes — first 8 bytes of every valid PNG file. */
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** Reporter sub-fields cap, per PROTOCOL.md § Metadata schema. */
const REPORTER_FIELD_MAX = 256

/** Title length bounds. */
const TITLE_MIN = 1
const TITLE_MAX = 200

export type ValidationResult =
  | { ok: true; value: BugReportCreate }
  | { ok: false; status: 400 | 422; envelope: { error: string; detail: string | unknown[] } }

/**
 * PNG magic-byte check. JPEG, GIF, WebP, and zero-byte buffers all fail.
 * Used in the intake handler before any disk write — the conformance
 * suite has an explicit JPEG-rejection test that this guards.
 */
export function isValidPngBuffer(buf: Buffer): boolean {
  if (buf.length < PNG_MAGIC.length) return false
  return buf.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)
}

/** Narrow type guard for "non-empty plain string". */
function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0
}

/**
 * Validate the metadata JSON object submitted in the multipart `metadata`
 * part. Returns either `{ok: true, value}` or `{ok: false, status, envelope}`.
 *
 * The split between `400 unsupported_protocol_version` and
 * `422 schema_error` matches the spec: an unrecognized protocol version
 * is a transport-layer mismatch, while every other failure is "we
 * understand the protocol but this payload is malformed."
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function validateSubmission(raw: any): ValidationResult {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    return {
      ok: false,
      status: 422,
      envelope: { error: 'schema_error', detail: 'metadata must be a JSON object' },
    }
  }

  // protocol_version — required, frozen to "0.1" in this revision.
  const submittedVersion = raw.protocol_version
  if (submittedVersion === undefined || submittedVersion === null || submittedVersion === '') {
    return {
      ok: false,
      status: 422,
      envelope: { error: 'schema_error', detail: 'protocol_version is required' },
    }
  }
  if (submittedVersion !== SUPPORTED_PROTOCOL_VERSION) {
    return {
      ok: false,
      status: 400,
      envelope: {
        error: 'unsupported_protocol_version',
        detail: `protocol_version "${String(submittedVersion)}" is not supported by this adapter`,
      },
    }
  }

  // title — required, 1..200 chars.
  if (!isNonEmptyString(raw.title)) {
    return {
      ok: false,
      status: 422,
      envelope: { error: 'schema_error', detail: 'title is required and must be a non-empty string' },
    }
  }
  if (raw.title.length < TITLE_MIN || raw.title.length > TITLE_MAX) {
    return {
      ok: false,
      status: 422,
      envelope: { error: 'schema_error', detail: `title length must be between ${TITLE_MIN} and ${TITLE_MAX} characters` },
    }
  }

  // client_ts — required, non-empty string. Format is not strictly
  // validated; PROTOCOL.md says the value is diagnostic only and the
  // server's received_at is authoritative.
  if (!isNonEmptyString(raw.client_ts)) {
    return {
      ok: false,
      status: 422,
      envelope: { error: 'schema_error', detail: 'client_ts is required and must be a non-empty ISO 8601 string' },
    }
  }

  // report_type — optional, default "bug".
  if (raw.report_type !== undefined) {
    if (typeof raw.report_type !== 'string' || !REPORT_TYPE_VALUES.includes(raw.report_type as never)) {
      return {
        ok: false,
        status: 422,
        envelope: {
          error: 'schema_error',
          detail: `report_type must be one of: ${REPORT_TYPE_VALUES.join(', ')}`,
        },
      }
    }
  }

  // severity — optional, default "medium". NEVER coerce silently.
  if (raw.severity !== undefined) {
    if (typeof raw.severity !== 'string' || !SEVERITY_VALUES.includes(raw.severity as never)) {
      return {
        ok: false,
        status: 422,
        envelope: {
          error: 'schema_error',
          detail: `severity must be one of: ${SEVERITY_VALUES.join(', ')}`,
        },
      }
    }
  }

  // tags — optional list of strings.
  if (raw.tags !== undefined) {
    if (!Array.isArray(raw.tags) || !raw.tags.every((t: unknown) => typeof t === 'string')) {
      return {
        ok: false,
        status: 422,
        envelope: { error: 'schema_error', detail: 'tags must be an array of strings' },
      }
    }
  }

  // reporter — optional, sub-fields capped at 256 chars each.
  if (raw.reporter !== undefined) {
    if (raw.reporter === null || typeof raw.reporter !== 'object' || Array.isArray(raw.reporter)) {
      return {
        ok: false,
        status: 422,
        envelope: { error: 'schema_error', detail: 'reporter must be an object' },
      }
    }
    for (const field of ['name', 'email', 'user_id'] as const) {
      const value = (raw.reporter as Record<string, unknown>)[field]
      if (value !== undefined) {
        if (typeof value !== 'string') {
          return {
            ok: false,
            status: 422,
            envelope: { error: 'schema_error', detail: `reporter.${field} must be a string` },
          }
        }
        if (value.length > REPORTER_FIELD_MAX) {
          return {
            ok: false,
            status: 422,
            envelope: {
              error: 'schema_error',
              detail: `reporter.${field} exceeds ${REPORTER_FIELD_MAX} characters`,
            },
          }
        }
      }
    }
  }

  // context — optional object; extra keys are preserved verbatim.
  if (raw.context !== undefined) {
    if (raw.context === null || typeof raw.context !== 'object' || Array.isArray(raw.context)) {
      return {
        ok: false,
        status: 422,
        envelope: { error: 'schema_error', detail: 'context must be an object' },
      }
    }
  }

  return { ok: true, value: raw as BugReportCreate }
}

/**
 * Validate a `PUT /reports/{id}/status` body. Same enum-rejection
 * discipline as severity — no silent coercion.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function validateStatusUpdate(raw: any):
  | { ok: true; value: BugReportStatusUpdate }
  | { ok: false; envelope: { error: string; detail: string } } {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, envelope: { error: 'schema_error', detail: 'request body must be a JSON object' } }
  }
  if (typeof raw.status !== 'string' || !STATUS_VALUES.includes(raw.status as never)) {
    return {
      ok: false,
      envelope: {
        error: 'schema_error',
        detail: `status must be one of: ${STATUS_VALUES.join(', ')}`,
      },
    }
  }
  for (const field of ['fix_commit', 'fix_description'] as const) {
    if (raw[field] !== undefined && typeof raw[field] !== 'string') {
      return { ok: false, envelope: { error: 'schema_error', detail: `${field} must be a string` } }
    }
  }
  return { ok: true, value: raw as BugReportStatusUpdate }
}

/** Report-id format check, mirroring the protocol regex. */
const REPORT_ID_RE = /^bug-[A-Za-z]?\d{3,}$/
export function isValidReportId(id: string): boolean {
  return REPORT_ID_RE.test(id)
}
