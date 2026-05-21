// Bug-Fab wire protocol v0.1 — TypeScript types for the Express adapter.
//
// Reference (PROSE):       https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md
// Reference (AUTHORITATIVE): https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json
//
// IMPORTANT: All field names are snake_case to match the wire protocol.
// Do NOT camelCase these — Bug-Fab routes use snake_case across the wire,
// in both request bodies and response bodies. (Pitfall #1 for Express + TS.)

export type Severity   = 'low' | 'medium' | 'high' | 'critical'
export type Status     = 'open' | 'investigating' | 'fixed' | 'closed'
export type ReportType = 'bug' | 'feature_request'
export type ProtocolVersion = '0.1'

export type LifecycleAction = 'created' | 'status_changed' | 'deleted' | 'archived'

// ---------- Submission (POST /bug-reports body) ----------

export interface Reporter {
  name?:    string
  email?:   string
  user_id?: string
}

export interface BugReportContext {
  url?:              string
  module?:           string
  user_agent?:       string  // client-reported; diagnostic only
  viewport_width?:   number
  viewport_height?:  number
  console_errors?:   Array<Record<string, unknown>>
  network_log?:      Array<Record<string, unknown>>
  source_mapping?:   Record<string, unknown>
  app_version?:      string
  environment?:      string

  // Schema allows additional properties on context (Pydantic extra="allow");
  // adapters MUST preserve them through round-trip.
  [extra: string]:   unknown
}

export interface BugReportSubmission {
  protocol_version:    ProtocolVersion  // MUST equal "0.1"; reject others with 400
  title:               string           // 1..200 chars (required)
  client_ts:           string           // non-empty ISO 8601 (required)
  report_type?:        ReportType       // default "bug"
  description?:        string           // optional, default ""
  expected_behavior?:  string           // optional, default ""
  severity?:           Severity         // default "medium"; strict reject on invalid
  tags?:               string[]
  reporter?:           Reporter
  context?:            BugReportContext
}

// ---------- Lifecycle ----------

export interface LifecycleEvent {
  action:           LifecycleAction
  by:               string
  at:               string
  status?:          Status
  fix_commit?:      string
  fix_description?: string
  // Spec allows additional properties on lifecycle entries.
  [extra: string]:  unknown
}

// ---------- Stored & response shapes ----------

export interface BugReportSummary {
  id:               string
  title:            string
  report_type:      ReportType
  severity:         Severity
  status:           Status
  module?:          string
  created_at:       string
  has_screenshot:   boolean
  github_issue_url: string | null
}

export interface BugReportDetail extends BugReportSummary {
  description:                 string
  expected_behavior:           string
  tags:                        string[]
  reporter:                    Reporter
  context:                     BugReportContext
  lifecycle:                   LifecycleEvent[]
  server_user_agent:           string
  client_reported_user_agent:  string
  environment:                 string
  client_ts:                   string
  protocol_version:            string
  updated_at:                  string
  github_issue_number:         number | null
}

export interface BugReportListStats {
  open:          number
  investigating: number
  fixed:         number
  closed:        number
}

export interface BugReportListResponse {
  items:     BugReportSummary[]
  total:     number
  page:      number
  page_size: number
  stats:     BugReportListStats
}

export interface BugReportIntakeResponse {
  id:               string
  received_at:      string
  stored_at:        string
  github_issue_url: string | null
}

export interface StatusUpdateRequest {
  status:           Status
  fix_commit?:      string
  fix_description?: string
}

export interface ListFilters {
  status?:           Status
  severity?:         Severity
  environment?:      string
  include_archived?: boolean
}

// ---------- IStorage contract ----------
//
// Mirrors the cross-adapter `IStorage` documented in
// https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md
// § "Adapter authorship checklist" item 3.
//
// 9 required methods + the optional `setGitHubIssue` post-save hook.

export interface SaveReportInput {
  protocol_version:            string
  title:                       string
  description:                 string
  expected_behavior:           string
  report_type:                 ReportType
  severity:                    Severity
  tags:                        string[]
  reporter:                    Reporter
  context:                     BugReportContext
  client_ts:                   string
  server_user_agent:           string
  client_reported_user_agent:  string
}

export interface IStorage {
  saveReport(metadata: SaveReportInput, screenshotBytes: Buffer): Promise<string>

  getReport(id: string): Promise<BugReportDetail | null>

  listReports(
    filters:  ListFilters,
    page:     number,
    pageSize: number,
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }>

  getScreenshotPath(id: string): Promise<string | null>

  updateStatus(
    id:              string,
    newStatus:       Status,
    by:              string,
    fixCommit?:      string,
    fixDescription?: string,
  ): Promise<BugReportDetail>

  deleteReport(id: string): Promise<void>
  archiveReport(id: string): Promise<void>
  bulkCloseFixed(): Promise<number>
  bulkArchiveClosed(): Promise<number>

  // Optional hook — adapters that persist GitHub issue refs implement this.
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>
}

// ---------- Router options ----------

export interface BugFabGitHubOptions {
  enabled: boolean
  pat:     string
  repo:    string             // "owner/repo"
  apiBase?: string            // default "https://api.github.com"
}

export interface BugFabRateLimitOptions {
  enabled:     boolean
  maxRequests: number
  windowMs:    number
}

export interface BugFabViewerPermissions {
  /** When false, PUT /reports/:id/status is not registered. Default true. */
  can_edit_status?: boolean
  /** When false, DELETE /reports/:id is not registered. Default true. */
  can_delete?:      boolean
  /** When false, /bulk-close-fixed and /bulk-archive-closed are not registered. Default true. */
  can_bulk?:        boolean
}

export interface BugFabRouterOptions {
  /**
   * Required. The persistence backend implementing the IStorage contract.
   */
  storage: IStorage

  /**
   * Optional. Best-effort GitHub Issues sync configuration.
   * If `enabled` is true and the PAT/repo are valid, intake will mirror
   * reports to issues. Failures are logged and never block intake.
   */
  github?: BugFabGitHubOptions

  /**
   * Optional. In-memory per-IP rate limiter. Disabled by default.
   * Not safe across multiple processes — use a reverse-proxy limiter
   * for clustered deployments.
   */
  rateLimit?: BugFabRateLimitOptions

  /**
   * Optional. Trims the viewer surface area — endpoints with their
   * permission set to false are not registered at all.
   *
   * v0.1 has no per-user auth; this is a coarse mount-time toggle.
   */
  viewerPermissions?: BugFabViewerPermissions

  /**
   * Optional. Maximum screenshot size in bytes. Defaults to 10 MiB.
   * The protocol caps at 10 MiB; adapters MAY enforce stricter limits.
   */
  maxScreenshotBytes?: number

  /**
   * Optional. Override the logger. Defaults to console-based logging.
   */
  logger?: Logger
}

export interface Logger {
  info(msg: string, ...args: unknown[]): void
  warn(msg: string, ...args: unknown[]): void
  error(msg: string, ...args: unknown[]): void
}
