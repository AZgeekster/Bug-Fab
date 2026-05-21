// Bug-Fab wire protocol v0.1 — TypeScript types
//
// Authoritative source: repo/docs/protocol-schema.json (JSON Schema, Draft 2020-12).
// Prose:                repo/docs/PROTOCOL.md.
// If a field disagrees, the schema wins. This file is a hand-maintained snapshot.
//
// All keys here mirror the wire-format snake_case exactly. We do NOT camelCase
// JSON keys at the boundary — adapters that translate keys lose round-trip
// fidelity for `context` extras.

export type Severity = 'low' | 'medium' | 'high' | 'critical';
export type Status = 'open' | 'investigating' | 'fixed' | 'closed';
export type ReportType = 'bug' | 'feature_request';
export type ProtocolVersion = '0.1';

export type LifecycleAction = 'created' | 'status_changed' | 'deleted' | 'archived';

export interface ConsoleEntry {
  level?: string;
  message?: string;
  stack?: string;
  ts?: string;
  [extra: string]: unknown;
}

export interface NetworkEntry {
  method?: string;
  url?: string;
  status?: number;
  duration_ms?: number;
  ts?: string;
  [extra: string]: unknown;
}

export interface BugReportContext {
  url?: string;
  module?: string;
  user_agent?: string; // client-reported; diagnostic only
  viewport_width?: number;
  viewport_height?: number;
  console_errors?: ConsoleEntry[];
  network_log?: NetworkEntry[];
  source_mapping?: Record<string, unknown>;
  app_version?: string;
  environment?: string;
  // Spec allows arbitrary extra keys (Pydantic extra="allow").
  [extraKey: string]: unknown;
}

export interface Reporter {
  name?: string;
  email?: string;
  user_id?: string;
}

// --- Intake ---

export interface BugReportSubmission {
  protocol_version: ProtocolVersion;
  title: string;
  client_ts: string;
  report_type?: ReportType;
  description?: string;
  expected_behavior?: string;
  severity?: Severity;
  tags?: string[];
  reporter?: Reporter;
  context?: BugReportContext;
}

// --- Lifecycle ---

export interface LifecycleEvent {
  action: LifecycleAction;
  // Schema default is "" for all string fields — emitters must populate empty
  // strings rather than omitting / nulling so the wire shape matches PROTOCOL.md.
  by: string;
  at: string;
  status?: Status;
  fix_commit: string;
  fix_description: string;
}

// --- Storage shape (server-enriched) ---

export interface StoredReport {
  id: string;
  title: string;
  description: string;
  expected_behavior?: string;
  report_type: ReportType;
  severity: Severity;
  status: Status;
  tags: string[];
  reporter: Reporter;
  context: BugReportContext;
  client_ts: string;
  protocol_version: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  server_user_agent: string;
  client_reported_user_agent?: string;
  github_issue_url: string | null;
  github_issue_number: number | null;
  lifecycle: LifecycleEvent[];
}

// --- Wire response shapes ---

export interface BugReportSummary {
  id: string;
  title: string;
  report_type: ReportType;
  severity: Severity;
  status: Status;
  // Schema default is "" — emitters MUST populate (never undefined). The
  // protocol-schema.json declares this as a `string` with `default: ""`,
  // so JSON.stringify omitting an undefined value is a wire drift.
  module: string;
  created_at: string;
  has_screenshot: boolean;
  github_issue_url: string | null;
}

export interface BugReportDetail extends BugReportSummary {
  description: string;
  // All string fields below have schema default "" per protocol-schema.json
  // §BugReportDetail. Emitters MUST populate (never undefined).
  expected_behavior: string;
  tags: string[];
  reporter: Reporter;
  context: BugReportContext;
  lifecycle: LifecycleEvent[];
  server_user_agent: string;
  client_reported_user_agent: string;
  environment: string;
  client_ts: string;
  protocol_version: string;
  updated_at: string;
  github_issue_number: number | null;
}

export interface BugReportListStats {
  open: number;
  investigating: number;
  fixed: number;
  closed: number;
}

export interface BugReportListResponse {
  items: BugReportSummary[];
  total: number;
  page: number;
  page_size: number;
  stats: BugReportListStats;
}

export interface BugReportIntakeResponse {
  id: string;
  received_at: string;
  stored_at: string;
  github_issue_url: string | null;
}

export interface StatusUpdateRequest {
  status: Status;
  fix_commit?: string;
  fix_description?: string;
}

export interface BulkCloseResponse {
  closed: number;
}

export interface BulkArchiveResponse {
  archived: number;
}

export interface ListFilters {
  status?: Status;
  severity?: Severity;
  environment?: string;
  include_archived?: boolean;
}

// --- Storage interface (per Adapter Authorship Checklist § 3) ---

export interface SaveReportInput {
  submission: BugReportSubmission;
  serverUserAgent: string;
  clientReportedUserAgent?: string;
  screenshotBytes: Uint8Array;
}

export interface IStorage {
  saveReport(input: SaveReportInput): Promise<{ id: string; storedAt: string; receivedAt: string }>;
  getReport(id: string): Promise<BugReportDetail | null>;
  listReports(
    filters: ListFilters,
    page: number,
    pageSize: number
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }>;
  getScreenshotPath(id: string): Promise<string | null>;
  getScreenshotBytes(id: string): Promise<Uint8Array | null>;
  updateStatus(
    id: string,
    newStatus: Status,
    by: string | null,
    fixCommit?: string,
    fixDescription?: string
  ): Promise<BugReportDetail>;
  deleteReport(id: string): Promise<void>;
  archiveReport(id: string): Promise<void>;
  bulkCloseFixed(): Promise<number>;
  bulkArchiveClosed(): Promise<number>;
  // Optional post-save hook for GitHub Issues sync.
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>;
}

// --- Handler factory options ---

export interface GitHubSyncOptions {
  enabled: boolean;
  pat: string;
  repo: string; // "owner/repo"
  apiBase?: string;
  labels?: string[];
}

export interface ViewerPermissions {
  can_edit_status?: boolean;
  can_delete?: boolean;
  can_bulk?: boolean;
}

export interface BugFabAdapterOptions {
  storage: IStorage;
  github?: GitHubSyncOptions;
  /**
   * Resolve the acting user identity from the SvelteKit request event for
   * lifecycle log entries. Return `null` for unauthenticated.
   * Optional — defaults to "anonymous" on intake and `null` elsewhere.
   */
  resolveActor?: (event: { request: Request; locals?: unknown }) => string | null | Promise<string | null>;
  /**
   * Maximum bytes for the screenshot PNG. Defaults to 10 MiB per spec.
   */
  maxScreenshotBytes?: number;
}
