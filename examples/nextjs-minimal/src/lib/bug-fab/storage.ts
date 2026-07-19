// File-backed Bug-Fab storage for the Next.js POC.
//
// Mirrors the behavior of the Python reference adapter's `FileStorage`
// (see `bug_fab/storage/files.py`). One report = one PNG + one JSON on
// disk plus a summary entry in `index.json`. Atomic writes via tmp +
// rename so a crash mid-write never publishes a torn file.
//
// Layout under `storage_dir`:
//
//   <storage_dir>/
//   ├── index.json             denormalized listing for fast list/filter
//   ├── bug-001.json           full report payload
//   ├── bug-001.png            screenshot
//   └── archive/
//       ├── bug-002.json       archived report
//       └── bug-002.png
//
// WHY no DB: the POC's deliberate scope is "prove the protocol works in
// a single Next.js process with zero external services." Real
// deployments behind Vercel / Cloudflare Pages need a different storage
// backend (S3/R2/KV) — this class is illustrative, not production code.
//
// Concurrency: a single in-memory promise chain serializes index reads
// and writes. This is process-local — it does NOT protect against two
// `next start` workers racing on the same directory. Single-process
// deployments only.

import { promises as fs } from 'node:fs'
import path from 'node:path'
import {
  type BugReportCreate,
  type BugReportDetail,
  type BugReportListResponse,
  type BugReportSummary,
  type LifecycleEvent,
  type ListFilters,
  type Status,
} from './types'

const INDEX_FILENAME = 'index.json'
const ARCHIVE_SUBDIR = 'archive'

interface IndexEntry {
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

interface IndexFile {
  reports: IndexEntry[]
  next_number: number
}

/** ISO 8601 in UTC — server clock is authoritative per PROTOCOL.md. */
function nowIso(): string {
  return new Date().toISOString()
}

async function atomicWriteText(filePath: string, payload: string): Promise<void> {
  const tmp = `${filePath}.tmp`
  await fs.writeFile(tmp, payload, 'utf8')
  await fs.rename(tmp, filePath)
}

async function atomicWriteBytes(filePath: string, payload: Buffer): Promise<void> {
  const tmp = `${filePath}.tmp`
  await fs.writeFile(tmp, payload)
  await fs.rename(tmp, filePath)
}

async function pathExists(p: string): Promise<boolean> {
  try {
    await fs.stat(p)
    return true
  } catch {
    return false
  }
}

/**
 * Disk-backed implementation of the Bug-Fab storage contract for the
 * Next.js minimal POC. Single-process only.
 */
export class FileStorage {
  readonly storageDir: string
  readonly archiveDir: string
  readonly idPrefix: string
  private readonly indexPath: string
  /** Tail-of-chain promise that serializes all index mutations. */
  private chain: Promise<unknown> = Promise.resolve()

  constructor(storageDir: string, idPrefix = '') {
    this.storageDir = path.resolve(storageDir)
    this.archiveDir = path.join(this.storageDir, ARCHIVE_SUBDIR)
    this.indexPath = path.join(this.storageDir, INDEX_FILENAME)
    this.idPrefix = idPrefix
  }

  /** Run `task` after every previously queued task. Provides FIFO mutex semantics. */
  private withLock<T>(task: () => Promise<T>): Promise<T> {
    const next = this.chain.then(task, task)
    // Swallow the resolved value on the chain so a failed task doesn't poison the next one.
    this.chain = next.catch(() => undefined)
    return next
  }

  async ensureDirs(): Promise<void> {
    await fs.mkdir(this.storageDir, { recursive: true })
    await fs.mkdir(this.archiveDir, { recursive: true })
  }

  // -------------------------------------------------------------------------
  // Public storage API
  // -------------------------------------------------------------------------

  async saveReport(
    metadata: BugReportCreate & { server_user_agent?: string; environment?: string },
    screenshotBytes: Buffer,
  ): Promise<string> {
    await this.ensureDirs()
    return this.withLock(async () => {
      const index = await this.readIndex()
      const reportId = this.nextId(index)
      const now = nowIso()
      const report = this.buildReport(reportId, metadata, now)
      await this.writeScreenshot(reportId, screenshotBytes)
      await this.writeReport(reportId, report)
      index.reports.push(this.buildIndexEntry(report))
      index.next_number += 1
      await this.writeIndex(index)
      return reportId
    })
  }

  async getReport(reportId: string): Promise<BugReportDetail | null> {
    return this.readReport(reportId)
  }

  async listReports(filters: ListFilters, page: number, pageSize: number): Promise<BugReportListResponse> {
    const index = await this.readIndex().catch(() => ({ reports: [], next_number: 1 }))
    const matched = index.reports.filter((entry) => this.matchesFilters(entry, filters))
    matched.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    const total = matched.length
    const start = Math.max(0, (page - 1) * pageSize)
    const items = matched.slice(start, start + pageSize) as BugReportSummary[]
    const stats = this.computeStats(index.reports)
    return { items, total, page, page_size: pageSize, stats }
  }

  async getScreenshotPath(reportId: string): Promise<string | null> {
    const live = path.join(this.storageDir, `${reportId}.png`)
    if (await pathExists(live)) return live
    const archived = path.join(this.archiveDir, `${reportId}.png`)
    if (await pathExists(archived)) return archived
    return null
  }

  async updateStatus(
    reportId: string,
    status: Status,
    fixCommit = '',
    fixDescription = '',
    by = 'viewer',
  ): Promise<BugReportDetail | null> {
    return this.withLock(async () => {
      const data = await this.readReport(reportId)
      if (data === null) return null
      data.status = status
      data.updated_at = nowIso()
      const event: LifecycleEvent = {
        action: 'status_changed',
        by,
        at: data.updated_at,
        status,
        fix_commit: fixCommit,
        fix_description: fixDescription,
      }
      data.lifecycle = [...(data.lifecycle ?? []), event]
      await this.writeReport(reportId, data)
      await this.updateIndexEntry(reportId, { status })
      return data
    })
  }

  async deleteReport(reportId: string): Promise<boolean> {
    return this.withLock(async () => {
      let removed = false
      for (const candidate of this.candidatePaths(reportId)) {
        if (await pathExists(candidate)) {
          await fs.unlink(candidate)
          removed = true
        }
      }
      if (removed) {
        const index = await this.readIndex()
        index.reports = index.reports.filter((e) => e.id !== reportId)
        await this.writeIndex(index)
      }
      return removed
    })
  }

  async bulkCloseFixed(by = 'viewer'): Promise<number> {
    const index = await this.readIndex().catch(() => ({ reports: [], next_number: 1 }))
    const ids = index.reports.filter((e) => e.status === 'fixed').map((e) => e.id)
    let closed = 0
    for (const id of ids) {
      const updated = await this.updateStatus(id, 'closed', '', '', by)
      if (updated !== null) closed += 1
    }
    return closed
  }

  async bulkArchiveClosed(): Promise<number> {
    return this.withLock(async () => {
      const index = await this.readIndex()
      const ids = index.reports.filter((e) => e.status === 'closed').map((e) => e.id)
      let archived = 0
      for (const id of ids) {
        if (await this.archiveOne(id)) archived += 1
      }
      return archived
    })
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private nextId(index: IndexFile): string {
    const n = index.next_number
    return `bug-${this.idPrefix}${String(n).padStart(3, '0')}`
  }

  private buildReport(
    reportId: string,
    metadata: BugReportCreate & { server_user_agent?: string; environment?: string },
    now: string,
  ): BugReportDetail {
    const context = { ...(metadata.context ?? {}) }
    const reporter = {
      name: metadata.reporter?.name ?? '',
      email: metadata.reporter?.email ?? '',
      user_id: metadata.reporter?.user_id ?? '',
    }
    const moduleName = (typeof context.module === 'string' ? context.module : '') || ''
    return {
      id: reportId,
      protocol_version: metadata.protocol_version,
      title: metadata.title,
      client_ts: metadata.client_ts,
      report_type: metadata.report_type ?? 'bug',
      description: metadata.description ?? '',
      expected_behavior: metadata.expected_behavior ?? '',
      severity: metadata.severity ?? 'medium',
      status: 'open',
      tags: [...(metadata.tags ?? [])],
      reporter,
      context,
      module: moduleName,
      created_at: now,
      updated_at: now,
      has_screenshot: true,
      server_user_agent: metadata.server_user_agent ?? '',
      client_reported_user_agent: typeof context.user_agent === 'string' ? context.user_agent : '',
      environment:
        metadata.environment ||
        (typeof context.environment === 'string' ? context.environment : '') ||
        '',
      github_issue_url: null,
      github_issue_number: null,
      lifecycle: [
        {
          action: 'created',
          by: 'anonymous',
          at: now,
          fix_commit: '',
          fix_description: '',
        },
      ],
    }
  }

  private buildIndexEntry(report: BugReportDetail): IndexEntry {
    return {
      id: report.id,
      title: report.title,
      report_type: report.report_type,
      severity: report.severity,
      status: report.status,
      module: report.module ?? '',
      created_at: report.created_at,
      has_screenshot: report.has_screenshot,
      github_issue_url: report.github_issue_url,
    }
  }

  private matchesFilters(entry: IndexEntry, filters: ListFilters): boolean {
    if (filters.status && entry.status !== filters.status) return false
    if (filters.severity && entry.severity !== filters.severity) return false
    if (filters.module && entry.module !== filters.module) return false
    return true
  }

  private computeStats(entries: IndexEntry[]): BugReportListResponse['stats'] {
    const stats = { open: 0, investigating: 0, fixed: 0, closed: 0 }
    for (const entry of entries) {
      if (entry.status in stats) {
        stats[entry.status as keyof typeof stats] += 1
      }
    }
    return stats
  }

  private async readIndex(): Promise<IndexFile> {
    try {
      const text = await fs.readFile(this.indexPath, 'utf8')
      const data = JSON.parse(text) as Partial<IndexFile>
      return {
        reports: Array.isArray(data.reports) ? (data.reports as IndexEntry[]) : [],
        next_number: typeof data.next_number === 'number' ? data.next_number : 1,
      }
    } catch {
      return { reports: [], next_number: 1 }
    }
  }

  private async writeIndex(index: IndexFile): Promise<void> {
    await atomicWriteText(this.indexPath, JSON.stringify(index, null, 2))
  }

  private async readReport(reportId: string): Promise<BugReportDetail | null> {
    for (const dir of [this.storageDir, this.archiveDir]) {
      const filePath = path.join(dir, `${reportId}.json`)
      if (await pathExists(filePath)) {
        const text = await fs.readFile(filePath, 'utf8')
        return JSON.parse(text) as BugReportDetail
      }
    }
    return null
  }

  private async writeReport(reportId: string, data: BugReportDetail): Promise<void> {
    let target = path.join(this.storageDir, `${reportId}.json`)
    if (!(await pathExists(target))) {
      const archived = path.join(this.archiveDir, `${reportId}.json`)
      if (await pathExists(archived)) target = archived
    }
    await atomicWriteText(target, JSON.stringify(data, null, 2))
  }

  private async writeScreenshot(reportId: string, screenshotBytes: Buffer): Promise<void> {
    const filePath = path.join(this.storageDir, `${reportId}.png`)
    await atomicWriteBytes(filePath, screenshotBytes)
  }

  private async updateIndexEntry(reportId: string, fields: Partial<IndexEntry>): Promise<void> {
    const index = await this.readIndex()
    for (const entry of index.reports) {
      if (entry.id === reportId) {
        Object.assign(entry, fields)
        break
      }
    }
    await this.writeIndex(index)
  }

  private candidatePaths(reportId: string): string[] {
    return [
      path.join(this.storageDir, `${reportId}.json`),
      path.join(this.storageDir, `${reportId}.png`),
      path.join(this.archiveDir, `${reportId}.json`),
      path.join(this.archiveDir, `${reportId}.png`),
    ]
  }

  private async archiveOne(reportId: string): Promise<boolean> {
    const jsonSrc = path.join(this.storageDir, `${reportId}.json`)
    const pngSrc = path.join(this.storageDir, `${reportId}.png`)
    const jsonExists = await pathExists(jsonSrc)
    const pngExists = await pathExists(pngSrc)
    if (!jsonExists && !pngExists) return false
    if (jsonExists) await fs.rename(jsonSrc, path.join(this.archiveDir, `${reportId}.json`))
    if (pngExists) await fs.rename(pngSrc, path.join(this.archiveDir, `${reportId}.png`))
    const index = await this.readIndex()
    index.reports = index.reports.filter((e) => e.id !== reportId)
    await this.writeIndex(index)
    return true
  }
}

// -------------------------------------------------------------------------
// Module-level singleton — Route Handlers import this directly.
// -------------------------------------------------------------------------
//
// WHY a singleton: the in-memory mutex chain that serializes index
// writes lives on the instance. If every Route Handler instantiated a
// fresh FileStorage, two concurrent requests could race on `index.json`
// and lose one writer's update. One process = one instance.
//
// Next.js's hot-reload in dev re-evaluates modules; cache the singleton
// on globalThis so HMR doesn't reset the lock chain mid-request.

const STORAGE_DIR = process.env.BUG_FAB_STORAGE_DIR ?? path.join(process.cwd(), 'bug_reports')
const ID_PREFIX = process.env.BUG_FAB_ID_PREFIX ?? ''

declare global {
  // eslint-disable-next-line no-var
  var __bugFabStorage: FileStorage | undefined
}

export const storage: FileStorage =
  globalThis.__bugFabStorage ?? (globalThis.__bugFabStorage = new FileStorage(STORAGE_DIR, ID_PREFIX))

// -------------------------------------------------------------------------
// GitHub Issues sync — OUT OF SCOPE for this POC.
// -------------------------------------------------------------------------
// The protocol allows adapters to optionally sync new reports to GitHub
// Issues; failures MUST log server-side and return `github_issue_url:
// null` (PROTOCOL.md § Failure modes that MUST NOT yield non-2xx). The
// Python reference adapter implements this in
// `bug_fab/integrations/github.py`. To add it here:
//
// 1. POST to `${GITHUB_API_BASE}/repos/${OWNER}/${REPO}/issues` with a
//    PAT in Authorization, body `{title, body, labels}`.
// 2. On success, persist `{github_issue_url, github_issue_number}` to
//    the stored report and return the URL in the intake response.
// 3. On failure, log and return `github_issue_url: null` — never fail
//    the intake.
//
// Deliberately omitted to keep the example focused on the wire protocol.
