// Validation helpers for the Bug-Fab v0.1 wire protocol.
//
// Reference: repo/docs/PROTOCOL.md, repo/docs/protocol-schema.json.
//
// IMPORTANT: do NOT silently coerce unknown enum values — the conformance
// suite explicitly rejects coercion (e.g., severity="urgent" must 422, not
// silently rewrite to "medium"). See PROTOCOL.md § Severity enum.

import type { Severity, Status, ReportType, BugReportSubmission } from './types.js';

export const VALID_SEVERITIES: readonly Severity[] = ['low', 'medium', 'high', 'critical'];
export const VALID_STATUSES: readonly Status[] = ['open', 'investigating', 'fixed', 'closed'];
export const VALID_REPORT_TYPES: readonly ReportType[] = ['bug', 'feature_request'];
export const SUPPORTED_PROTOCOL_VERSIONS: readonly string[] = ['0.1'];

export const MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024; // 10 MiB
export const MAX_TITLE_LENGTH = 200;
export const MAX_REPORTER_FIELD_LENGTH = 256;
export const MAX_PAGE_SIZE = 200;
export const DEFAULT_PAGE_SIZE = 20;

// PNG magic bytes (8). Adapters that accept other image types fail conformance.
const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

export function isValidPngMagic(buf: Uint8Array): boolean {
  if (buf.length < PNG_MAGIC.length) return false;
  for (let i = 0; i < PNG_MAGIC.length; i++) {
    if (buf[i] !== PNG_MAGIC[i]) return false;
  }
  return true;
}

// Path-traversal guard. Report IDs are `bug-NNN`, optionally carrying a
// single-letter environment prefix (`bug-P001`). `event.params.id` reaches a
// filesystem join inside FileStorage, so an id outside this shape must be
// rejected with 404 before storage sees it.
//
// Identical to the reference implementation's `_REPORT_ID_RE` and the Hono
// adapter's `REPORT_ID_RE`. Do not re-derive it.
const REPORT_ID_RE = /^bug-[A-Za-z]?\d{1,12}$/;

export function isValidReportId(id: unknown): id is string {
  return typeof id === 'string' && REPORT_ID_RE.test(id);
}

export function isValidSeverity(v: unknown): v is Severity {
  return typeof v === 'string' && (VALID_SEVERITIES as readonly string[]).includes(v);
}

export function isValidStatus(v: unknown): v is Status {
  return typeof v === 'string' && (VALID_STATUSES as readonly string[]).includes(v);
}

export function isValidReportType(v: unknown): v is ReportType {
  return typeof v === 'string' && (VALID_REPORT_TYPES as readonly string[]).includes(v);
}

export function isSupportedProtocolVersion(v: unknown): v is string {
  return typeof v === 'string' && SUPPORTED_PROTOCOL_VERSIONS.includes(v);
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  /** When set, caller should map to 400 unsupported_protocol_version (not 422). */
  unsupportedProtocolVersion?: string;
}

/**
 * Validate intake submission metadata. Returns a structured result; callers
 * map the result to HTTP error envelopes.
 */
export function validateSubmission(raw: unknown): ValidationResult {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, errors: ['metadata must be a JSON object'] };
  }

  const obj = raw as Record<string, unknown>;
  const errors: string[] = [];

  // protocol_version — required, only "0.1" accepted in this revision.
  if (!('protocol_version' in obj)) {
    return { ok: false, errors: ['protocol_version is required'], unsupportedProtocolVersion: 'missing' };
  }
  if (!isSupportedProtocolVersion(obj.protocol_version)) {
    return {
      ok: false,
      errors: [`unsupported protocol_version: ${String(obj.protocol_version)}`],
      unsupportedProtocolVersion: String(obj.protocol_version)
    };
  }

  // title — required, 1..200 chars.
  if (typeof obj.title !== 'string' || obj.title.trim().length === 0) {
    errors.push('title is required and must be a non-empty string');
  } else if (obj.title.length > MAX_TITLE_LENGTH) {
    errors.push(`title must not exceed ${MAX_TITLE_LENGTH} characters`);
  }

  // client_ts — required, non-empty (format opaque per spec).
  if (typeof obj.client_ts !== 'string' || obj.client_ts.length === 0) {
    errors.push('client_ts is required and must be a non-empty ISO 8601 string');
  }

  // description — optional in v0.1.
  if ('description' in obj && obj.description !== undefined && typeof obj.description !== 'string') {
    errors.push('description must be a string when supplied');
  }

  // expected_behavior — optional.
  if ('expected_behavior' in obj && obj.expected_behavior !== undefined && typeof obj.expected_behavior !== 'string') {
    errors.push('expected_behavior must be a string when supplied');
  }

  // severity — only validate if supplied; do NOT coerce.
  if ('severity' in obj && obj.severity !== undefined && !isValidSeverity(obj.severity)) {
    errors.push(
      `severity must be one of: ${VALID_SEVERITIES.join(', ')}. Got: "${String(obj.severity)}"`
    );
  }

  // report_type — only validate if supplied; do NOT coerce.
  if ('report_type' in obj && obj.report_type !== undefined && !isValidReportType(obj.report_type)) {
    errors.push(
      `report_type must be one of: ${VALID_REPORT_TYPES.join(', ')}. Got: "${String(obj.report_type)}"`
    );
  }

  // tags — array of strings.
  if ('tags' in obj && obj.tags !== undefined) {
    if (!Array.isArray(obj.tags) || obj.tags.some((t) => typeof t !== 'string')) {
      errors.push('tags must be an array of strings when supplied');
    }
  }

  // reporter — optional, sub-fields capped at 256 chars.
  if ('reporter' in obj && obj.reporter !== undefined) {
    if (typeof obj.reporter !== 'object' || obj.reporter === null || Array.isArray(obj.reporter)) {
      errors.push('reporter must be an object with optional name/email/user_id sub-fields');
    } else {
      const reporter = obj.reporter as Record<string, unknown>;
      for (const sub of ['name', 'email', 'user_id'] as const) {
        const v = reporter[sub];
        if (v !== undefined) {
          if (typeof v !== 'string') {
            errors.push(`reporter.${sub} must be a string when supplied`);
          } else if (v.length > MAX_REPORTER_FIELD_LENGTH) {
            errors.push(`reporter.${sub} must not exceed ${MAX_REPORTER_FIELD_LENGTH} characters`);
          }
        }
      }
    }
  }

  // context — optional object, but if present must be an object (extras allowed).
  if ('context' in obj && obj.context !== undefined) {
    if (typeof obj.context !== 'object' || obj.context === null || Array.isArray(obj.context)) {
      errors.push('context must be an object when supplied');
    }
  }

  return { ok: errors.length === 0, errors };
}

export function validateStatusUpdate(raw: unknown): ValidationResult {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, errors: ['request body must be a JSON object'] };
  }
  const obj = raw as Record<string, unknown>;
  const errors: string[] = [];

  if (!('status' in obj) || obj.status === undefined) {
    errors.push('status is required');
  } else if (!isValidStatus(obj.status)) {
    errors.push(`status must be one of: ${VALID_STATUSES.join(', ')}. Got: "${String(obj.status)}"`);
  }

  if ('fix_commit' in obj && obj.fix_commit !== undefined && typeof obj.fix_commit !== 'string') {
    errors.push('fix_commit must be a string when supplied');
  }
  if ('fix_description' in obj && obj.fix_description !== undefined && typeof obj.fix_description !== 'string') {
    errors.push('fix_description must be a string when supplied');
  }

  return { ok: errors.length === 0, errors };
}

/** Cast validated object back to typed BugReportSubmission. */
export function asSubmission(raw: unknown): BugReportSubmission {
  return raw as BugReportSubmission;
}
