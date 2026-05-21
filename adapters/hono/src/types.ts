// Bug-Fab wire-protocol v0.1 TypeScript types for the Hono adapter.
//
// Authoritative spec:
//   - JSON Schema:  https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json
//   - Prose:        https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md
//
// All wire-level field names are snake_case; do NOT camelCase them in
// serialization. Internal-only TypeScript identifiers (function names,
// option keys for the package itself) follow standard TS camelCase.
//
// Edge-runtime constraint:
//   Screenshot bytes are carried as `Uint8Array` (Web standard), NOT
//   Node's `Buffer`. Cloudflare Workers, Deno Deploy, and Vercel Edge
//   do not expose `Buffer` natively. Bun and Node both implement
//   `Uint8Array` so the type works everywhere.

export type Severity = 'low' | 'medium' | 'high' | 'critical'
export type Status = 'open' | 'investigating' | 'fixed' | 'closed'
export type ReportType = 'bug' | 'feature_request'
export type ProtocolVersion = '0.1'

// Auto-captured browser context. The schema is `additionalProperties: true`
// — consumer-supplied diagnostic fields are preserved verbatim through
// round-trip per `docs/PROTOCOL.md` § Storage round-trip notes.
export interface BugReportContext {
  url?: string
  module?: string
  /** Client-reported user agent. Diagnostic only — `server_user_agent`
   *  on the stored report is the source of truth. */
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

// Optional submitter identity. Sub-fields are opaque strings capped at
// 256 chars by the validator (see validation.ts).
export interface Reporter {
  name?: string
  email?: string
  user_id?: string
}

// --------- Intake ---------

export interface BugReportSubmission {
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

// What the storage layer receives from the intake handler.
export interface StoredMetadata extends BugReportSubmission {
  server_user_agent: string
  client_reported_user_agent?: string
}

// --------- Lifecycle ---------

export interface LifecycleEvent {
  action: 'created' | 'status_changed' | 'deleted' | 'archived'
  by: string | null
  at: string
  status?: Status
  fix_commit?: string
  fix_description?: string
  [extraKey: string]: unknown
}

// --------- Response shapes ---------

export interface BugReportSummary {
  id: string
  title: string
  report_type: ReportType
  severity: Severity
  status: Status
  module: string
  created_at: string
  has_screenshot: boolean
  github_issue_url: string | null
}

export interface BugReportDetail extends BugReportSummary {
  description: string
  expected_behavior: string
  tags: string[]
  reporter: Reporter
  context: BugReportContext
  lifecycle: LifecycleEvent[]
  server_user_agent: string
  client_reported_user_agent: string
  environment: string
  client_ts: string
  protocol_version: string
  updated_at: string
  github_issue_number: number | null
}

export interface BugReportListStats {
  open: number
  investigating: number
  fixed: number
  closed: number
}

export interface BugReportListResponse {
  items: BugReportSummary[]
  total: number
  page: number
  page_size: number
  stats: BugReportListStats
}

// Minimal `201 Created` body — see PROTOCOL.md § Response. Adapters MUST
// NOT echo user-submitted free text here (privacy: response bodies leak
// to reverse-proxy logs and browser network panels).
export interface BugReportIntakeResponse {
  id: string
  received_at: string
  stored_at: string
  github_issue_url: string | null
}

export interface StatusUpdateRequest {
  status: Status
  fix_commit?: string
  fix_description?: string
}

export interface ListFilters {
  status?: Status
  severity?: Severity
  environment?: string
  include_archived?: boolean
}

// --------- Storage interface ---------

export interface IStorage {
  saveReport(metadata: StoredMetadata, screenshotBytes: Uint8Array): Promise<string>

  getReport(id: string): Promise<BugReportDetail | null>

  listReports(
    filters: ListFilters,
    page: number,
    pageSize: number,
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }>

  /**
   * Return the raw PNG bytes for a report's screenshot, or null if missing.
   * Edge runtimes have no `node:fs` — the abstraction returns bytes
   * directly so the storage class chooses where they came from
   * (R2 object, KV value, in-memory map, etc.).
   */
  getScreenshotBytes(id: string): Promise<Uint8Array | null>

  updateStatus(
    id: string,
    newStatus: Status,
    by: string,
    fixCommit?: string,
    fixDescription?: string,
  ): Promise<BugReportDetail>

  deleteReport(id: string): Promise<void>
  archiveReport(id: string): Promise<void>
  bulkCloseFixed(): Promise<number>
  bulkArchiveClosed(): Promise<number>

  /** Optional post-save hook for GitHub Issues sync. Duck-typed at call site. */
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>
}

// --------- App / plugin options ---------

export interface BugFabGitHubOptions {
  enabled: boolean
  pat: string
  /** "owner/repo" */
  repo: string
  apiBase?: string
}

export interface BugFabRateLimitOptions {
  enabled: boolean
  /** Max requests per IP per window. */
  maxRequests: number
  /** Sliding window in ms. */
  windowMs: number
}

export interface BugFabViewerPermissions {
  can_edit_status: boolean
  can_delete: boolean
  can_bulk: boolean
}

export interface BugFabAppOptions {
  storage: IStorage
  /** Default "/api". Mounted as the prefix for the intake route. */
  submitPrefix?: string
  /** Default "/admin/bug-reports". MUST be non-empty and non-root —
   *  the viewer's HTML index lives at the prefix root. */
  viewerPrefix?: string
  github?: BugFabGitHubOptions
  rateLimit?: BugFabRateLimitOptions
  /**
   * Per-route gating. v0.1 has no auth abstraction — these flags
   * disable destructive viewer endpoints on top of mount-point auth.
   * Default: all true.
   */
  viewerPermissions?: Partial<BugFabViewerPermissions>
  /**
   * Optional CSP nonce provider for viewer HTML script tags. Returning
   * `null` (or throwing) renders without a nonce attribute — the
   * browser will refuse the inline script under strict CSP, which is
   * the intended visible failure mode. See repo/docs/CSP.md.
   */
  cspNonce?: (req: Request) => string | null
}
