// Bug-Fab v0.1 input validation for the Express adapter.
//
// Reference: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md
// Authoritative schema: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json
//
// Design notes:
//   - PNG-only: the protocol does not support JPEG. A JPEG submission MUST
//     be rejected with 415 unsupported_media_type. Magic-byte check below.
//   - Strict reject (no silent coercion) on severity / status / report_type.
//     The conformance suite explicitly verifies that "urgent" → 422, NOT
//     silently rewritten to "medium".
//   - protocol_version === "0.1" is required; missing or unknown → 400
//     unsupported_protocol_version (not 422 schema_error).
//   - reporter.{name,email,user_id} are opaque strings, each capped at 256.

import type { Severity, Status, ReportType } from './types.js'

export const VALID_SEVERITIES: readonly Severity[] = ['low', 'medium', 'high', 'critical']
export const VALID_STATUSES:   readonly Status[]   = ['open', 'investigating', 'fixed', 'closed']
export const VALID_REPORT_TYPES: readonly ReportType[] = ['bug', 'feature_request']

export const SUPPORTED_PROTOCOL_VERSION = '0.1'

export const DEFAULT_MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024  // 10 MiB
export const MAX_TITLE_LENGTH             = 200
export const MAX_REPORTER_FIELD_LENGTH    = 256

// Magic-byte signature for PNG. JPEG (0xFFD8...) and GIF (0x47494638) are
// rejected. Reference: https://www.w3.org/TR/PNG/#5PNG-file-signature
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

export function isValidPngBuffer(buf: Buffer): boolean {
  if (buf.length < PNG_MAGIC.length) return false
  return buf.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)
}

// Path-traversal guard. Report IDs are `bug-NNN`, optionally carrying a
// single-letter environment prefix (`bug-P001`). Every `:id` route feeds its
// parameter to a filesystem join or a storage lookup, so an id that escapes
// this shape must be rejected with 404 before it reaches either.
//
// Identical to the reference implementation's `_REPORT_ID_RE` and the Hono
// adapter's `REPORT_ID_RE`. Do not re-derive it — the audit found this
// pattern written four times with two incompatible bodies.
const REPORT_ID_RE = /^bug-[A-Za-z]?\d{1,12}$/

export function isValidReportId(id: unknown): id is string {
  return typeof id === 'string' && REPORT_ID_RE.test(id)
}

export function isValidSeverity(v: unknown): v is Severity {
  return typeof v === 'string' && (VALID_SEVERITIES as readonly string[]).includes(v)
}

export function isValidStatus(v: unknown): v is Status {
  return typeof v === 'string' && (VALID_STATUSES as readonly string[]).includes(v)
}

export function isValidReportType(v: unknown): v is ReportType {
  return typeof v === 'string' && (VALID_REPORT_TYPES as readonly string[]).includes(v)
}

export function isValidProtocolVersion(v: unknown): boolean {
  return v === SUPPORTED_PROTOCOL_VERSION
}

export interface ValidationResult {
  ok:     boolean
  errors: string[]
}

export const PROTOCOL_VERSION_SENTINEL = '__unsupported_protocol_version__'

export function validateSubmission(raw: unknown): ValidationResult {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, errors: ['metadata must be a JSON object'] }
  }

  const obj = raw as Record<string, unknown>
  const errors: string[] = []

  // protocol_version — required, must equal "0.1".
  // Sentinel-prefixed so the caller can map to 400 unsupported_protocol_version
  // (rather than the generic 422 schema_error path).
  if (!('protocol_version' in obj)) {
    return { ok: false, errors: [`${PROTOCOL_VERSION_SENTINEL}:missing`] }
  }
  if (!isValidProtocolVersion(obj.protocol_version)) {
    return { ok: false, errors: [`${PROTOCOL_VERSION_SENTINEL}:${String(obj.protocol_version)}`] }
  }

  // title — required, 1..200 chars (after trimming whitespace from the
  // ends to detect blank submissions, but length cap on the original).
  if (typeof obj.title !== 'string' || obj.title.trim().length === 0) {
    errors.push('title is required and must be a non-empty string')
  } else if (obj.title.length > MAX_TITLE_LENGTH) {
    errors.push(`title must not exceed ${MAX_TITLE_LENGTH} characters`)
  }

  // client_ts — required non-empty string. ISO 8601 not validated — the
  // protocol treats it as opaque diagnostic data.
  if (typeof obj.client_ts !== 'string' || obj.client_ts.length === 0) {
    errors.push('client_ts is required and must be a non-empty ISO 8601 string')
  }

  // description / expected_behavior — optional strings.
  if ('description' in obj && obj.description !== undefined && typeof obj.description !== 'string') {
    errors.push('description must be a string when supplied')
  }
  if ('expected_behavior' in obj && obj.expected_behavior !== undefined && typeof obj.expected_behavior !== 'string') {
    errors.push('expected_behavior must be a string when supplied')
  }

  // severity — strict reject on invalid (no silent coercion).
  if ('severity' in obj && obj.severity !== undefined && !isValidSeverity(obj.severity)) {
    errors.push(
      `severity must be one of: ${VALID_SEVERITIES.join(', ')}. Got: "${String(obj.severity)}"`,
    )
  }

  // report_type — strict reject on invalid.
  if ('report_type' in obj && obj.report_type !== undefined && !isValidReportType(obj.report_type)) {
    errors.push(
      `report_type must be one of: ${VALID_REPORT_TYPES.join(', ')}. Got: "${String(obj.report_type)}"`,
    )
  }

  // tags — array of string when supplied.
  if ('tags' in obj && obj.tags !== undefined) {
    if (!Array.isArray(obj.tags) || obj.tags.some((t) => typeof t !== 'string')) {
      errors.push('tags must be an array of strings when supplied')
    }
  }

  // reporter sub-fields — opaque strings, each capped at 256 chars.
  if ('reporter' in obj && obj.reporter !== undefined) {
    if (typeof obj.reporter !== 'object' || obj.reporter === null || Array.isArray(obj.reporter)) {
      errors.push('reporter must be an object with optional name/email/user_id sub-fields')
    } else {
      const reporter = obj.reporter as Record<string, unknown>
      for (const sub of ['name', 'email', 'user_id'] as const) {
        const v = reporter[sub]
        if (v !== undefined) {
          if (typeof v !== 'string') {
            errors.push(`reporter.${sub} must be a string when supplied`)
          } else if (v.length > MAX_REPORTER_FIELD_LENGTH) {
            errors.push(
              `reporter.${sub} must not exceed ${MAX_REPORTER_FIELD_LENGTH} characters`,
            )
          }
        }
      }
    }
  }

  // context — must be an object when supplied. Extra keys are allowed.
  if ('context' in obj && obj.context !== undefined) {
    if (typeof obj.context !== 'object' || obj.context === null || Array.isArray(obj.context)) {
      errors.push('context must be an object when supplied')
    }
  }

  return { ok: errors.length === 0, errors }
}

export function validateStatusUpdate(raw: unknown): ValidationResult {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, errors: ['request body must be a JSON object'] }
  }

  const obj = raw as Record<string, unknown>
  const errors: string[] = []

  if (!('status' in obj) || obj.status === undefined) {
    errors.push('status is required')
  } else if (!isValidStatus(obj.status)) {
    errors.push(
      `status must be one of: ${VALID_STATUSES.join(', ')}. Got: "${String(obj.status)}"`,
    )
  }

  if ('fix_commit' in obj && obj.fix_commit !== undefined && typeof obj.fix_commit !== 'string') {
    errors.push('fix_commit must be a string when supplied')
  }
  if ('fix_description' in obj && obj.fix_description !== undefined && typeof obj.fix_description !== 'string') {
    errors.push('fix_description must be a string when supplied')
  }

  return { ok: errors.length === 0, errors }
}
