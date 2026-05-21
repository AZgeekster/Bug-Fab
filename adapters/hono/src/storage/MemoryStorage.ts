// In-memory storage backend.
//
// Use cases:
//   - Tests (unit, integration, conformance harness).
//   - Local POC where ephemeral state is acceptable.
//   - A safe default that doesn't accidentally write to disk on edge runtimes.
//
// NOT suitable for any production deployment — the entire dataset is
// lost when the worker / process recycles.

import type {
  IStorage,
  StoredMetadata,
  BugReportDetail,
  BugReportSummary,
  BugReportListStats,
  ListFilters,
  Status,
  Severity,
  ReportType,
  LifecycleEvent,
} from '../types.js'

interface InternalRecord {
  detail: BugReportDetail
  screenshot: Uint8Array
  archived: boolean
}

export interface MemoryStorageOptions {
  /** Optional ID prefix for environment tagging — `bug-P038`, `bug-D012`. */
  idPrefix?: string
}

export class MemoryStorage implements IStorage {
  private records: Map<string, InternalRecord> = new Map()
  private nextSeq = 1
  private readonly idPrefix: string

  constructor(opts: MemoryStorageOptions = {}) {
    this.idPrefix = opts.idPrefix ?? ''
  }

  private nextId(): string {
    const n = String(this.nextSeq++).padStart(3, '0')
    return `bug-${this.idPrefix}${n}`
  }

  async saveReport(meta: StoredMetadata, screenshotBytes: Uint8Array): Promise<string> {
    const id = this.nextId()
    const now = new Date().toISOString()
    const ctx = { ...(meta.context ?? {}) }
    const detail: BugReportDetail = {
      id,
      title: meta.title,
      report_type: (meta.report_type ?? 'bug') as ReportType,
      severity: (meta.severity ?? 'medium') as Severity,
      status: 'open',
      module: typeof ctx['module'] === 'string' ? (ctx['module'] as string) : '',
      created_at: now,
      has_screenshot: true,
      github_issue_url: null,
      description: meta.description ?? '',
      expected_behavior: meta.expected_behavior ?? '',
      tags: meta.tags ?? [],
      reporter: meta.reporter ?? {},
      context: ctx,
      lifecycle: [
        {
          action: 'created',
          by: 'anonymous',
          at: now,
          status: 'open',
        } satisfies LifecycleEvent,
      ],
      server_user_agent: meta.server_user_agent,
      client_reported_user_agent:
        meta.client_reported_user_agent ??
        (typeof ctx['user_agent'] === 'string' ? (ctx['user_agent'] as string) : ''),
      environment: typeof ctx['environment'] === 'string' ? (ctx['environment'] as string) : '',
      client_ts: meta.client_ts,
      protocol_version: meta.protocol_version,
      updated_at: now,
      github_issue_number: null,
    }

    this.records.set(id, {
      detail,
      screenshot: new Uint8Array(screenshotBytes),
      archived: false,
    })
    return id
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    const rec = this.records.get(id)
    if (!rec) return null
    return rec.detail
  }

  async listReports(
    filters: ListFilters,
    page: number,
    pageSize: number,
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }> {
    let visible = Array.from(this.records.values())
    if (!filters.include_archived) visible = visible.filter((r) => !r.archived)
    if (filters.status) visible = visible.filter((r) => r.detail.status === filters.status)
    if (filters.severity) visible = visible.filter((r) => r.detail.severity === filters.severity)
    if (filters.environment) {
      visible = visible.filter((r) => r.detail.environment === filters.environment)
    }

    const stats: BugReportListStats = {
      open: 0,
      investigating: 0,
      fixed: 0,
      closed: 0,
    }
    for (const r of visible) {
      const s = r.detail.status
      if (s === 'open' || s === 'investigating' || s === 'fixed' || s === 'closed') {
        stats[s]++
      }
    }

    visible.sort((a, b) => b.detail.created_at.localeCompare(a.detail.created_at))
    const total = visible.length
    const start = Math.max(0, (page - 1) * pageSize)
    const slice = visible.slice(start, start + pageSize)

    const items: BugReportSummary[] = slice.map((r) => ({
      id: r.detail.id,
      title: r.detail.title,
      report_type: r.detail.report_type,
      severity: r.detail.severity,
      status: r.detail.status,
      module: r.detail.module,
      created_at: r.detail.created_at,
      has_screenshot: r.detail.has_screenshot,
      github_issue_url: r.detail.github_issue_url,
    }))

    return { items, total, stats }
  }

  async getScreenshotBytes(id: string): Promise<Uint8Array | null> {
    const rec = this.records.get(id)
    if (!rec) return null
    return rec.screenshot
  }

  async updateStatus(
    id: string,
    newStatus: Status,
    by: string,
    fixCommit?: string,
    fixDescription?: string,
  ): Promise<BugReportDetail> {
    const rec = this.records.get(id)
    if (!rec) throw new Error(`report ${id} not found`)

    const at = new Date().toISOString()
    rec.detail.status = newStatus
    rec.detail.updated_at = at

    const event: LifecycleEvent = { action: 'status_changed', by, at, status: newStatus }
    if (fixCommit) event.fix_commit = fixCommit
    if (fixDescription) event.fix_description = fixDescription
    rec.detail.lifecycle.push(event)

    return rec.detail
  }

  async deleteReport(id: string): Promise<void> {
    if (!this.records.has(id)) throw new Error(`report ${id} not found`)
    this.records.delete(id)
  }

  async archiveReport(id: string): Promise<void> {
    const rec = this.records.get(id)
    if (!rec) throw new Error(`report ${id} not found`)
    rec.archived = true
    const at = new Date().toISOString()
    rec.detail.updated_at = at
    rec.detail.lifecycle.push({ action: 'archived', by: null, at })
  }

  async bulkCloseFixed(): Promise<number> {
    let n = 0
    for (const rec of this.records.values()) {
      if (rec.detail.status === 'fixed') {
        const at = new Date().toISOString()
        rec.detail.status = 'closed'
        rec.detail.updated_at = at
        rec.detail.lifecycle.push({
          action: 'status_changed',
          by: null,
          at,
          status: 'closed',
        })
        n++
      }
    }
    return n
  }

  async bulkArchiveClosed(): Promise<number> {
    let n = 0
    for (const rec of this.records.values()) {
      if (rec.detail.status === 'closed' && !rec.archived) {
        rec.archived = true
        const at = new Date().toISOString()
        rec.detail.updated_at = at
        rec.detail.lifecycle.push({ action: 'archived', by: null, at })
        n++
      }
    }
    return n
  }

  async setGitHubIssue(id: string, issueUrl: string, issueNumber: number): Promise<void> {
    const rec = this.records.get(id)
    if (!rec) return
    rec.detail.github_issue_url = issueUrl
    rec.detail.github_issue_number = issueNumber
  }
}
