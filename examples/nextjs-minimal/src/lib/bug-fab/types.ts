// Bug-Fab wire protocol v0.1 — TypeScript type snapshot.
//
// This file is a verbatim copy of `repo/types/protocol.d.ts`. The JSON
// Schema at `repo/docs/protocol-schema.json` is the authoritative
// contract; this file is a developer convenience for adapter authors.
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
export type Severity = 'low' | 'medium' | 'high' | 'critical'

/**
 * Status workflow. Adapters MUST reject unknown values on **write** with
 * `422 schema_error`. On **read**, adapters MUST accept any deprecated value
 * (e.g., a legacy `"resolved"`) so historical data stays renderable.
 */
export type Status = 'open' | 'investigating' | 'fixed' | 'closed'

/** Frozen Literal — adapters MUST reject other values with `422 schema_error`. */
export type ReportType = 'bug' | 'feature_request'

/** Frozen Literal — only `"0.1"` is valid in this protocol revision. */
export type ProtocolVersion = '0.1'

export const SEVERITY_VALUES: readonly Severity[] = ['low', 'medium', 'high', 'critical'] as const
export const STATUS_VALUES: readonly Status[] = ['open', 'investigating', 'fixed', 'closed'] as const
export const REPORT_TYPE_VALUES: readonly ReportType[] = ['bug', 'feature_request'] as const
export const SUPPORTED_PROTOCOL_VERSION: ProtocolVersion = '0.1'

// ----------------------------------------------------------------------------
// Submission payload (intake — `POST /bug-reports`, multipart `metadata` field)
// ----------------------------------------------------------------------------

export interface BugReportContext {
  url?: string
  module?: string
  user_agent?: string
  viewport_width?: number
  viewport_height?: number
  console_errors?: Array<Record<string, unknown>>
  network_log?: Array<Record<string, unknown>>
  source_mapping?: Record<string, unknown>
  app_version?: string
  environment?: string
  [extraKey: string]: unknown
}

export interface Reporter {
  name?: string
  email?: string
  user_id?: string
}

export interface BugReportCreate {
  protocol_version: ProtocolVersion
  title: string
  client_ts: string
  report_type?: ReportType
  description?: string
  expected_behavior?: string
  severity?: Severity
  tags?: string[]
  reporter?: Reporter
  context?: BugReportContext
}

// ----------------------------------------------------------------------------
// Lifecycle audit log (per report, append-only)
// ----------------------------------------------------------------------------

export interface LifecycleEvent {
  action: string
  by: string
  at: string
  status?: Status
  fix_commit?: string
  fix_description?: string
  [extraKey: string]: unknown
}

// ----------------------------------------------------------------------------
// Response shapes (read paths)
// ----------------------------------------------------------------------------

export interface BugReportSummary {
  id: string
  title: string
  report_type: string
  severity: string
  status: string
  module?: string
  created_at: string
  has_screenshot: boolean
  github_issue_url: string | null
}

export interface BugReportDetail extends BugReportSummary {
  description: string
  expected_behavior?: string
  tags: string[]
  reporter: Reporter
  context: BugReportContext
  lifecycle: LifecycleEvent[]
  server_user_agent: string
  client_reported_user_agent?: string
  environment?: string
  client_ts?: string
  protocol_version: string
  updated_at?: string
  github_issue_number?: number | null
}

export interface BugReportListResponse {
  items: BugReportSummary[]
  total: number
  page: number
  page_size: number
  stats: {
    open: number
    investigating: number
    fixed: number
    closed: number
  }
}

// ----------------------------------------------------------------------------
// Status update body (`PUT /reports/{id}/status`)
// ----------------------------------------------------------------------------

export interface BugReportStatusUpdate {
  status: Status
  fix_commit?: string
  fix_description?: string
}

// ----------------------------------------------------------------------------
// Intake response (`POST /bug-reports` → 201)
// ----------------------------------------------------------------------------

export interface BugReportIntakeResponse {
  id: string
  received_at: string
  stored_at: string
  github_issue_url: string | null
}

// ----------------------------------------------------------------------------
// Error envelope (every non-2xx response except 204)
// ----------------------------------------------------------------------------

export interface BugFabError {
  error: string
  detail: string | unknown[]
  [extraKey: string]: unknown
}

// ----------------------------------------------------------------------------
// List query params
// ----------------------------------------------------------------------------

export interface ListFilters {
  status?: Status | string
  severity?: Severity | string
  environment?: string
  include_archived?: boolean
  module?: string
  page?: number
  page_size?: number
}
