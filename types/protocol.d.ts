// Bug-Fab wire protocol v0.1 — TypeScript type snapshot.
//
// This file is a **snapshot** of the protocol shape, hand-maintained to
// match `bug_fab/schemas.py` and `docs/protocol-schema.json`. The JSON
// Schema is the authoritative contract; this file is a developer
// convenience for TypeScript adapter authors who want types in their IDE
// without writing them from scratch.
//
// If this file disagrees with `protocol-schema.json` or PROTOCOL.md,
// the JSON Schema wins. File a bug if you spot drift.
//
// Spec: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md
// JSON Schema: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json

// ----------------------------------------------------------------------------
// Locked enums
// ----------------------------------------------------------------------------

/** Severity vocabulary. Adapters MUST reject other values with `422 schema_error`. */
export type Severity = 'low' | 'medium' | 'high' | 'critical';

/**
 * Status workflow. Adapters MUST reject unknown values on **write** with
 * `422 schema_error`. On **read**, adapters MUST accept any deprecated value
 * (e.g., a legacy `"resolved"`) so historical data stays renderable.
 */
export type Status = 'open' | 'investigating' | 'fixed' | 'closed';

/** Frozen Literal — adapters MUST reject other values with `422 schema_error`. */
export type ReportType = 'bug' | 'feature_request';

/** Frozen Literal — only `"0.1"` is valid in this protocol revision. */
export type ProtocolVersion = '0.1';

// ----------------------------------------------------------------------------
// Submission payload (intake — `POST /bug-reports`, multipart `metadata` field)
// ----------------------------------------------------------------------------

/**
 * Auto-captured browser context attached to every submission.
 *
 * - All sub-fields are optional and default to empty values.
 * - The object accepts **arbitrary extra keys**. Consumer-specific
 *   diagnostic fields are preserved verbatim through round-trip — this
 *   is how a consumer attaches things like `feature_flag_state` or
 *   `release_train` without protocol changes.
 */
export interface BugReportContext {
  url?:               string;   // Recommended cap 2 KiB; adapters MAY truncate.
  module?:            string;   // Consumer-defined logical area.
  user_agent?:        string;   // Client-reported. Diagnostic only — see PROTOCOL.md §User-Agent trust boundary.
  viewport_width?:    number;
  viewport_height?:   number;
  console_errors?:    Array<Record<string, unknown>>;
  network_log?:       Array<Record<string, unknown>>;
  source_mapping?:    Record<string, unknown>;
  app_version?:       string;
  environment?:       string;   // dev / staging / prod / etc. — consumer-defined.

  // Arbitrary extra keys are preserved on round-trip.
  [extraKey: string]: unknown;
}

/** Optional submitter identity. Each sub-field is opaque, capped at 256 chars. */
export interface Reporter {
  name?:    string;
  email?:   string;
  user_id?: string;
}

/**
 * Submission body (the JSON in the `metadata` multipart field).
 *
 * Required fields: `protocol_version`, `title`, `client_ts`. Adapters
 * MUST reject submissions missing any of these with `422 schema_error`.
 */
export interface BugReportCreate {
  protocol_version:    ProtocolVersion;
  title:               string;     // 1–200 chars
  client_ts:           string;     // ISO 8601, non-empty
  report_type?:        ReportType; // default "bug"
  description?:        string;
  expected_behavior?:  string;
  severity?:           Severity;   // default "medium"
  tags?:               string[];
  reporter?:           Reporter;
  context?:            BugReportContext;
}

// ----------------------------------------------------------------------------
// Lifecycle audit log (per report, append-only)
// ----------------------------------------------------------------------------

export interface LifecycleEvent {
  /** `created` | `status_changed` | `deleted` | `archived` (forward-additive). */
  action:           string;
  /**
   * Submitter / actor identity. `"anonymous"` or empty string is the
   * sentinel for "no auth context." Consumers reading the log MUST treat
   * `"anonymous"`, `""`, and `null` as equivalent.
   */
  by:               string;
  at:               string;       // ISO 8601
  status?:          Status;       // present on action="status_changed"
  fix_commit?:      string;
  fix_description?: string;

  // Arbitrary extra keys preserved on round-trip.
  [extraKey: string]: unknown;
}

// ----------------------------------------------------------------------------
// Response shapes (read paths)
// ----------------------------------------------------------------------------

/** Compact summary used by `GET /reports`. */
export interface BugReportSummary {
  id:               string;
  title:            string;
  report_type:      string;       // 'bug' | 'feature_request' (deprecated values accepted on read)
  severity:         string;       // see Severity type — read paths accept deprecated values
  status:           string;       // see Status type — read paths accept deprecated values
  module?:          string;
  created_at:       string;       // ISO 8601
  has_screenshot:   boolean;
  github_issue_url: string | null;
}

/** Full report payload returned by `GET /reports/{id}` and `PUT /reports/{id}/status`. */
export interface BugReportDetail extends BugReportSummary {
  description:                  string;
  expected_behavior?:           string;
  tags:                         string[];
  reporter:                     Reporter;
  context:                      BugReportContext;
  lifecycle:                    LifecycleEvent[];
  server_user_agent:            string;
  client_reported_user_agent?:  string;
  environment?:                 string;
  client_ts?:                   string;
  protocol_version:             string;       // version under which the report was submitted
  updated_at?:                  string;
  github_issue_number?:         number | null;
}

/** Pagination + stats envelope returned by `GET /reports`. */
export interface BugReportListResponse {
  items:     BugReportSummary[];
  total:     number;
  page:      number;
  page_size: number;
  stats: {
    open:          number;
    investigating: number;
    fixed:         number;
    closed:        number;
  };
}

// ----------------------------------------------------------------------------
// Status update body (`PUT /reports/{id}/status`)
// ----------------------------------------------------------------------------

export interface BugReportStatusUpdate {
  status:           Status;
  fix_commit?:      string;
  fix_description?: string;
}

// ----------------------------------------------------------------------------
// Intake response (`POST /bug-reports` → 201)
// ----------------------------------------------------------------------------

export interface BugReportIntakeResponse {
  /** Server-assigned ID, format `bug-{prefix?}NNN` (e.g., `bug-001`, `bug-P038`). */
  id:               string;
  received_at:      string;       // ISO 8601, server clock
  /** Opaque persistence reference (URI or vendor-specific string). Treat as opaque. */
  stored_at:        string;
  github_issue_url: string | null;
}

// ----------------------------------------------------------------------------
// Error envelope (every non-2xx response except 204)
// ----------------------------------------------------------------------------

export interface BugFabError {
  error:  string;          // machine-readable code (see PROTOCOL.md §Error responses)
  detail: string | unknown[];
}

// ----------------------------------------------------------------------------
// List query params (`GET /reports?status=...&severity=...&...`)
// ----------------------------------------------------------------------------

export interface ListFilters {
  status?:           Status;
  severity?:         Severity;
  environment?:      string;
  include_archived?: boolean;
  module?:           string;     // matches `context.module` exact-string
  page?:             number;     // 1-indexed
  page_size?:        number;     // capped at 200
}
