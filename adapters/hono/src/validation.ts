// Wire-protocol input validation for the Hono adapter.
//
// Validation rules pinned to docs/protocol-schema.json (authoritative)
// and docs/PROTOCOL.md (commentary):
//   - protocol_version MUST equal "0.1"; unknown → 400 unsupported_protocol_version.
//   - title required, 1..200 chars.
//   - client_ts required non-empty string (ISO 8601 not parsed; opaque diagnostic).
//   - severity / status / report_type strict-reject on unknown values
//     (silent coercion fails conformance — see PROTOCOL.md § Severity enum).
//   - reporter sub-fields capped at 256 chars each.
//   - PNG magic-byte check on the screenshot (Content-Type alone is
//     untrusted — conformance suite has an explicit JPEG-rejection test).
//
// IMPORTANT: do NOT camelCase any field name in this file. Wire format
// is snake_case end-to-end.

import type { Severity, Status, ReportType } from './types.js'

export const VALID_SEVERITIES: readonly Severity[] = [
  'low',
  'medium',
  'high',
  'critical',
]

export const VALID_STATUSES: readonly Status[] = [
  'open',
  'investigating',
  'fixed',
  'closed',
]

export const VALID_REPORT_TYPES: readonly ReportType[] = ['bug', 'feature_request']

export const SUPPORTED_PROTOCOL_VERSIONS: readonly string[] = ['0.1']

export const MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024 // 10 MiB
export const MAX_TOTAL_REQUEST_BYTES = 11 * 1024 * 1024 // 11 MiB recommendation
export const MAX_TITLE_LENGTH = 200
export const MAX_REPORTER_FIELD_LENGTH = 256

// PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

export function isValidPng(buf: Uint8Array): boolean {
  if (buf.byteLength < PNG_MAGIC.length) return false
  for (let i = 0; i < PNG_MAGIC.length; i++) {
    if (buf[i] !== PNG_MAGIC[i]) return false
  }
  return true
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

export function isSupportedProtocolVersion(v: unknown): boolean {
  return typeof v === 'string' && SUPPORTED_PROTOCOL_VERSIONS.includes(v)
}

export type ValidationKind = 'unsupported_protocol_version' | 'schema_error'

export interface ValidationOk {
  ok: true
}

export interface ValidationFail {
  ok: false
  kind: ValidationKind
  /** First-class machine-readable list of per-field errors. */
  errors: string[]
}

export type ValidationResult = ValidationOk | ValidationFail

/**
 * Validate the parsed JSON metadata object from a `POST /bug-reports`
 * intake. Returns either `{ ok: true }` or a structured failure with a
 * `kind` discriminator the caller maps to either 400 or 422.
 *
 * NOTE: this validator does NOT mutate or coerce. The caller applies
 * defaults (`severity` → "medium", `report_type` → "bug", etc.) AFTER
 * a successful validation.
 */
export function validateSubmission(raw: unknown): ValidationResult {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, kind: 'schema_error', errors: ['metadata must be a JSON object'] }
  }

  const obj = raw as Record<string, unknown>

  // protocol_version — required and equal to "0.1". Missing or unknown
  // value short-circuits to a 400 with the dedicated error code.
  if (!('protocol_version' in obj)) {
    return {
      ok: false,
      kind: 'unsupported_protocol_version',
      errors: ['protocol_version is required'],
    }
  }
  if (!isSupportedProtocolVersion(obj['protocol_version'])) {
    return {
      ok: false,
      kind: 'unsupported_protocol_version',
      errors: [String(obj['protocol_version'])],
    }
  }

  const errors: string[] = []

  // title — required, 1..200 chars.
  if (
    typeof obj['title'] !== 'string' ||
    obj['title'].trim().length === 0
  ) {
    errors.push('title is required and must be a non-empty string')
  } else if (obj['title'].length > MAX_TITLE_LENGTH) {
    errors.push(`title must not exceed ${MAX_TITLE_LENGTH} characters`)
  }

  // client_ts — required non-empty string.
  if (typeof obj['client_ts'] !== 'string' || obj['client_ts'].length === 0) {
    errors.push('client_ts is required and must be a non-empty ISO 8601 string')
  }

  // description / expected_behavior — optional strings.
  for (const f of ['description', 'expected_behavior'] as const) {
    if (f in obj && obj[f] !== undefined && typeof obj[f] !== 'string') {
      errors.push(`${f} must be a string when supplied`)
    }
  }

  // severity — only validate when supplied; do NOT silent-coerce.
  if ('severity' in obj && obj['severity'] !== undefined) {
    if (!isValidSeverity(obj['severity'])) {
      errors.push(
        `severity must be one of: ${VALID_SEVERITIES.join(', ')}. Got: "${String(
          obj['severity'],
        )}"`,
      )
    }
  }

  // report_type — only validate when supplied.
  if ('report_type' in obj && obj['report_type'] !== undefined) {
    if (!isValidReportType(obj['report_type'])) {
      errors.push(
        `report_type must be one of: ${VALID_REPORT_TYPES.join(', ')}. Got: "${String(
          obj['report_type'],
        )}"`,
      )
    }
  }

  // tags — array of strings when supplied.
  if ('tags' in obj && obj['tags'] !== undefined) {
    if (!Array.isArray(obj['tags']) || !obj['tags'].every((t) => typeof t === 'string')) {
      errors.push('tags must be an array of strings')
    }
  }

  // reporter — object with optional name / email / user_id, each capped.
  if ('reporter' in obj && obj['reporter'] !== undefined) {
    const reporter = obj['reporter']
    if (typeof reporter !== 'object' || reporter === null || Array.isArray(reporter)) {
      errors.push('reporter must be an object with optional name/email/user_id sub-fields')
    } else {
      const r = reporter as Record<string, unknown>
      for (const sub of ['name', 'email', 'user_id'] as const) {
        const v = r[sub]
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

  // context — object with arbitrary extra keys allowed; no per-field cap
  // here because the schema is `additionalProperties: true`.
  if ('context' in obj && obj['context'] !== undefined) {
    if (
      typeof obj['context'] !== 'object' ||
      obj['context'] === null ||
      Array.isArray(obj['context'])
    ) {
      errors.push('context must be an object when supplied')
    }
  }

  if (errors.length > 0) {
    return { ok: false, kind: 'schema_error', errors }
  }
  return { ok: true }
}

/** Validate the body of `PUT /reports/:id/status`. Returns the same
 *  shape as `validateSubmission` but without the protocol_version axis. */
export function validateStatusUpdate(raw: unknown): ValidationResult {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return {
      ok: false,
      kind: 'schema_error',
      errors: ['request body must be a JSON object'],
    }
  }

  const obj = raw as Record<string, unknown>
  const errors: string[] = []

  if (!('status' in obj) || obj['status'] === undefined) {
    errors.push('status is required')
  } else if (!isValidStatus(obj['status'])) {
    errors.push(
      `status must be one of: ${VALID_STATUSES.join(', ')}. Got: "${String(
        obj['status'],
      )}"`,
    )
  }

  for (const f of ['fix_commit', 'fix_description'] as const) {
    if (f in obj && obj[f] !== undefined && typeof obj[f] !== 'string') {
      errors.push(`${f} must be a string when supplied`)
    }
  }

  if (errors.length > 0) {
    return { ok: false, kind: 'schema_error', errors }
  }
  return { ok: true }
}

/** Path-traversal guard for report IDs. Mirrors bug_fab/routers/viewer.py's
 *  `^bug-[A-Za-z]?\d{1,12}$`. */
const REPORT_ID_RE = /^bug-[A-Za-z]?\d{1,12}$/

export function isValidReportId(id: string): boolean {
  return REPORT_ID_RE.test(id)
}
