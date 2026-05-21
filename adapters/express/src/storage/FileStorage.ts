// FileStorage — zero-dependency JSON-on-disk backend.
//
// Layout:
//   {storageDir}/{reportId}/metadata.json
//   {storageDir}/{reportId}/screenshot.png
//   {storageDir}/archive/{reportId}/...   (after bulk-archive-closed)
//
// Atomic metadata writes via temp-file-then-rename. Loads an in-memory
// index on construction so list/filter ops do not hit the filesystem.
//
// Multi-process caveat: the in-memory counter and index are not safe for
// concurrent multi-process writers. For PM2 / clustered deployments,
// implement a SQL-backed IStorage instead. See the README's "Custom
// backends" section for the IStorage contract.

import {
  copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync,
  renameSync, rmSync, statSync, writeFileSync,
} from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'

import type {
  IStorage, SaveReportInput, BugReportDetail, BugReportSummary,
  BugReportListStats, ListFilters, Status, LifecycleEvent, ReportType, Severity,
  Reporter, BugReportContext,
} from '../types.js'

interface StoredReport {
  id:                          string
  protocol_version:            string
  title:                       string
  description:                 string
  expected_behavior:           string
  report_type:                 ReportType
  severity:                    Severity
  status:                      Status
  tags:                        string[]
  reporter:                    Reporter
  context:                     BugReportContext
  client_ts:                   string
  server_user_agent:           string
  client_reported_user_agent:  string
  created_at:                  string
  updated_at:                  string
  archived_at:                 string | null
  github_issue_url:            string | null
  github_issue_number:         number | null
  lifecycle:                   LifecycleEvent[]
}

function nowIso(): string {
  return new Date().toISOString()
}

function pad(n: number, width = 3): string {
  return String(n).padStart(width, '0')
}

function summaryFrom(r: StoredReport): BugReportSummary {
  const summary: BugReportSummary = {
    id:               r.id,
    title:            r.title,
    report_type:      r.report_type,
    severity:         r.severity,
    status:           r.status,
    created_at:       r.created_at,
    has_screenshot:   true,
    github_issue_url: r.github_issue_url,
  }
  if (r.context?.module && typeof r.context.module === 'string') {
    summary.module = r.context.module
  }
  return summary
}

function detailFrom(r: StoredReport): BugReportDetail {
  return {
    id:                          r.id,
    title:                       r.title,
    report_type:                 r.report_type,
    severity:                    r.severity,
    status:                      r.status,
    module:                      typeof r.context?.module === 'string' ? r.context.module : '',
    created_at:                  r.created_at,
    has_screenshot:              true,
    github_issue_url:            r.github_issue_url,
    description:                 r.description,
    expected_behavior:           r.expected_behavior,
    tags:                        r.tags,
    reporter:                    r.reporter,
    context:                     r.context,
    lifecycle:                   r.lifecycle,
    server_user_agent:           r.server_user_agent,
    client_reported_user_agent:  r.client_reported_user_agent,
    environment:                 typeof r.context?.environment === 'string' ? r.context.environment : '',
    client_ts:                   r.client_ts,
    protocol_version:            r.protocol_version,
    updated_at:                  r.updated_at,
    github_issue_number:         r.github_issue_number,
  }
}

export interface FileStorageOptions {
  storageDir: string
  /** Optional ID prefix character — `"P"` produces ids like `bug-P001`. */
  idPrefix?:  string
}

export class FileStorage implements IStorage {
  private readonly dir:    string
  private readonly prefix: string
  private counter = 0
  // Active (non-archived) reports — backs `getReport`, default `listReports`.
  private index = new Map<string, StoredReport>()
  // Archived reports — surfaced only when `listReports` is called with
  // `include_archived=true`. Loaded from `<dir>/archive/<id>/metadata.json`
  // on construction and kept in sync by `archiveReport`. The archived map
  // is intentionally separate from the active map so default listings
  // never have to filter archived rows out and the archive view never
  // accidentally falls back into the live mutation paths.
  private archivedIndex = new Map<string, StoredReport>()

  constructor(opts: FileStorageOptions) {
    this.dir    = resolve(opts.storageDir)
    this.prefix = opts.idPrefix ?? ''
    mkdirSync(this.dir, { recursive: true })
    this.loadIndex()
  }

  private loadIndex(): void {
    let entries
    try {
      entries = readdirSync(this.dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue
      if (entry.name === 'archive') {
        this.loadArchiveIndex()
        continue
      }
      const metaPath = join(this.dir, entry.name, 'metadata.json')
      if (!existsSync(metaPath)) continue
      try {
        const r = JSON.parse(readFileSync(metaPath, 'utf8')) as StoredReport
        this.index.set(r.id, r)
        this.bumpCounter(r.id)
      } catch {
        // Corrupt metadata file — skip and continue loading the rest.
      }
    }
  }

  /**
   * Walk `<dir>/archive/<id>/metadata.json` and populate `archivedIndex`.
   *
   * Counter bookkeeping must include archived ids too, otherwise a process
   * restart that finds only archived reports on disk would assign duplicate
   * ids on the next `saveReport` call.
   */
  private loadArchiveIndex(): void {
    const archiveRoot = join(this.dir, 'archive')
    if (!existsSync(archiveRoot)) return
    let archiveEntries
    try {
      archiveEntries = readdirSync(archiveRoot, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of archiveEntries) {
      if (!entry.isDirectory()) continue
      const metaPath = join(archiveRoot, entry.name, 'metadata.json')
      if (!existsSync(metaPath)) continue
      try {
        const r = JSON.parse(readFileSync(metaPath, 'utf8')) as StoredReport
        this.archivedIndex.set(r.id, r)
        this.bumpCounter(r.id)
      } catch {
        // Corrupt archived metadata — skip and continue.
      }
    }
  }

  private bumpCounter(id: string): void {
    const match = id.match(/(\d+)$/)
    if (match) {
      const n = parseInt(match[1]!, 10)
      if (n > this.counter) this.counter = n
    }
  }

  private nextId(): string {
    this.counter += 1
    const n = pad(this.counter, Math.max(3, String(this.counter).length))
    return this.prefix ? `bug-${this.prefix}${n}` : `bug-${n}`
  }

  private reportDir(id: string):       string { return join(this.dir, id) }
  private metaPath(id: string):        string { return join(this.reportDir(id), 'metadata.json') }
  private screenshotPath(id: string):  string { return join(this.reportDir(id), 'screenshot.png') }

  private writeMeta(report: StoredReport): void {
    const dest = this.metaPath(report.id)
    const tmp  = `${dest}.tmp-${randomUUID()}`
    writeFileSync(tmp, JSON.stringify(report, null, 2), 'utf8')
    renameSync(tmp, dest)
    this.index.set(report.id, report)
  }

  /** Returns an opaque file:// URI for the stored report directory. */
  storedAtFor(id: string): string {
    return `file://${this.reportDir(id).replace(/\\/g, '/')}/`
  }

  async saveReport(metadata: SaveReportInput, screenshotBytes: Buffer): Promise<string> {
    const id = this.nextId()
    const ts = nowIso()

    mkdirSync(this.reportDir(id), { recursive: true })
    writeFileSync(this.screenshotPath(id), screenshotBytes)

    const report: StoredReport = {
      id,
      protocol_version:            metadata.protocol_version,
      title:                       metadata.title,
      description:                 metadata.description,
      expected_behavior:           metadata.expected_behavior,
      report_type:                 metadata.report_type,
      severity:                    metadata.severity,
      status:                      'open',
      tags:                        metadata.tags,
      reporter:                    metadata.reporter,
      context:                     metadata.context,
      client_ts:                   metadata.client_ts,
      server_user_agent:           metadata.server_user_agent,
      client_reported_user_agent:  metadata.client_reported_user_agent,
      created_at:                  ts,
      updated_at:                  ts,
      archived_at:                 null,
      github_issue_url:            null,
      github_issue_number:         null,
      lifecycle: [{ action: 'created', by: 'anonymous', at: ts }],
    }

    this.writeMeta(report)
    return id
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    const r = this.index.get(id)
    return r ? detailFrom(r) : null
  }

  async listReports(
    filters:  ListFilters,
    page:     number,
    pageSize: number,
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }> {
    // include_archived=true merges the archive index into the visible set.
    // The active index never contains archived rows (they move to
    // archivedIndex on archiveReport) so the default listing has zero
    // overhead from the archive feature.
    const visible: StoredReport[] = filters.include_archived
      ? [...this.index.values(), ...this.archivedIndex.values()]
      : [...this.index.values()]

    let rows = visible
    if (filters.status)      rows = rows.filter((r) => r.status === filters.status)
    if (filters.severity)    rows = rows.filter((r) => r.severity === filters.severity)
    if (filters.environment) {
      rows = rows.filter((r) => {
        const env = r.context?.environment
        return typeof env === 'string' && env === filters.environment
      })
    }

    // Stats are computed across the filtered-but-pre-status-cut dataset so
    // the dashboard counters reflect "what would be visible given this set
    // of filters minus the status one." This matches the reference adapter.
    const statsBase = visible.filter((r) => {
      if (filters.severity && r.severity !== filters.severity) return false
      if (filters.environment) {
        const env = r.context?.environment
        if (typeof env !== 'string' || env !== filters.environment) return false
      }
      return true
    })
    const stats: BugReportListStats = {
      open:          statsBase.filter((r) => r.status === 'open').length,
      investigating: statsBase.filter((r) => r.status === 'investigating').length,
      fixed:         statsBase.filter((r) => r.status === 'fixed').length,
      closed:        statsBase.filter((r) => r.status === 'closed').length,
    }

    rows.sort((a, b) => b.created_at.localeCompare(a.created_at))
    const total = rows.length
    const items = rows
      .slice((page - 1) * pageSize, page * pageSize)
      .map(summaryFrom)

    return { items, total, stats }
  }

  async getScreenshotPath(id: string): Promise<string | null> {
    const p = this.screenshotPath(id)
    return existsSync(p) ? p : null
  }

  async updateStatus(
    id:              string,
    newStatus:       Status,
    by:              string,
    fixCommit?:      string,
    fixDescription?: string,
  ): Promise<BugReportDetail> {
    const r = this.index.get(id)
    if (!r) throw new Error(`Report not found: ${id}`)

    const at = nowIso()
    const entry: LifecycleEvent = {
      action: 'status_changed',
      by,
      at,
      status: newStatus,
    }
    if (fixCommit !== undefined)      entry.fix_commit      = fixCommit
    if (fixDescription !== undefined) entry.fix_description = fixDescription

    const updated: StoredReport = {
      ...r,
      status:     newStatus,
      updated_at: at,
      lifecycle:  [...r.lifecycle, entry],
    }

    this.writeMeta(updated)
    return detailFrom(updated)
  }

  async deleteReport(id: string): Promise<void> {
    if (!this.index.has(id)) throw new Error(`Report not found: ${id}`)
    const dir = this.reportDir(id)
    if (existsSync(dir)) rmSync(dir, { recursive: true, force: true })
    this.index.delete(id)
  }

  async archiveReport(id: string): Promise<void> {
    const r = this.index.get(id)
    if (!r) throw new Error(`Report not found: ${id}`)

    const archiveDir = join(this.dir, 'archive', id)
    mkdirSync(archiveDir, { recursive: true })

    const srcMeta = this.metaPath(id)
    const srcShot = this.screenshotPath(id)
    if (existsSync(srcMeta)) copyFileSync(srcMeta, join(archiveDir, 'metadata.json'))
    if (existsSync(srcShot)) copyFileSync(srcShot, join(archiveDir, 'screenshot.png'))

    // Apply archived_at + lifecycle entry, persist into the archive dir.
    const at = nowIso()
    const archived: StoredReport = {
      ...r,
      archived_at: at,
      lifecycle: [...r.lifecycle, { action: 'archived', by: 'system', at }],
    }
    const tmp = join(archiveDir, `metadata.json.tmp-${randomUUID()}`)
    writeFileSync(tmp, JSON.stringify(archived, null, 2), 'utf8')
    renameSync(tmp, join(archiveDir, 'metadata.json'))

    const srcDir = this.reportDir(id)
    if (existsSync(srcDir)) rmSync(srcDir, { recursive: true, force: true })
    this.index.delete(id)
    // Track the archived report so `listReports({ include_archived: true })`
    // can surface it. Without this, the on-disk archive folder would be the
    // only record and the JSON list endpoint would silently no-op.
    this.archivedIndex.set(id, archived)
  }

  async bulkCloseFixed(): Promise<number> {
    const fixed = [...this.index.values()].filter((r) => r.status === 'fixed' && !r.archived_at)
    for (const r of fixed) {
      await this.updateStatus(r.id, 'closed', 'system')
    }
    return fixed.length
  }

  async bulkArchiveClosed(): Promise<number> {
    const closed = [...this.index.values()].filter((r) => r.status === 'closed' && !r.archived_at)
    for (const r of closed) {
      await this.archiveReport(r.id)
    }
    return closed.length
  }

  async setGitHubIssue(id: string, issueUrl: string, issueNumber: number): Promise<void> {
    const r = this.index.get(id)
    if (!r) return
    const updated: StoredReport = {
      ...r,
      github_issue_url:    issueUrl,
      github_issue_number: issueNumber,
    }
    this.writeMeta(updated)
  }

  /** Test helper — number of currently-loaded reports. */
  size(): number {
    return this.index.size
  }

  /** Test helper — bytes written for a report's screenshot, or 0 if missing. */
  screenshotSize(id: string): number {
    const p = this.screenshotPath(id)
    if (!existsSync(p)) return 0
    return statSync(p).size
  }
}
