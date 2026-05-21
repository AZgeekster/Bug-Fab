// Cloudflare R2 + KV storage backend.
//
// Architecture:
//   - PNG screenshots → R2 (object storage; large blobs, range reads).
//   - Report metadata + lifecycle log → KV (small JSON blobs, fast reads).
//
// Why split? Workers KV has a 25 MiB per-value cap which is fine for
// metadata but tight for high-DPI screenshots; R2 has no per-object
// practical cap and is cheaper per byte. Separating storage classes also
// lets the viewer's `/reports/:id/screenshot` endpoint stream straight
// from R2 without a metadata fetch.
//
// This class is duck-typed against the Cloudflare bindings — we don't
// import `@cloudflare/workers-types` at module-load to avoid pulling
// runtime-specific types into Bun / Node consumers. Construct it with
// the bindings the runtime exposes:
//
//     export default {
//       async fetch(req, env: { BUG_FAB_R2: R2Bucket; BUG_FAB_KV: KVNamespace }) {
//         const storage = new R2Storage({ bucket: env.BUG_FAB_R2, kv: env.BUG_FAB_KV })
//         const app = createBugFabApp({ storage })
//         return app.fetch(req)
//       }
//     }

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

// Minimal structural types — duck-typed so this file compiles without
// `@cloudflare/workers-types` installed at module-load time.
export interface R2Bucket {
  put(key: string, value: ArrayBuffer | Uint8Array | ReadableStream): Promise<unknown>
  get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null>
  delete(key: string | string[]): Promise<void>
  list(opts?: { prefix?: string; cursor?: string; limit?: number }): Promise<{
    objects: Array<{ key: string }>
    truncated: boolean
    cursor?: string
  }>
}

export interface KVNamespace {
  put(key: string, value: string): Promise<void>
  get(key: string): Promise<string | null>
  delete(key: string): Promise<void>
  list(opts?: { prefix?: string; cursor?: string; limit?: number }): Promise<{
    keys: Array<{ name: string }>
    list_complete: boolean
    cursor?: string
  }>
}

export interface R2StorageOptions {
  bucket: R2Bucket
  kv: KVNamespace
  /** Optional ID prefix for environment tagging. */
  idPrefix?: string
  /** KV key prefix for the metadata blobs. Default `"bug-fab:report:"`. */
  metadataKeyPrefix?: string
  /** R2 key prefix for screenshot blobs. Default `"bug-fab/screenshots/"`. */
  screenshotKeyPrefix?: string
  /** KV key for the monotonic ID counter. Default `"bug-fab:counter"`. */
  counterKey?: string
}

export class R2Storage implements IStorage {
  private readonly bucket: R2Bucket
  private readonly kv: KVNamespace
  private readonly idPrefix: string
  private readonly metaPrefix: string
  private readonly screenshotPrefix: string
  private readonly counterKey: string

  constructor(opts: R2StorageOptions) {
    this.bucket = opts.bucket
    this.kv = opts.kv
    this.idPrefix = opts.idPrefix ?? ''
    this.metaPrefix = opts.metadataKeyPrefix ?? 'bug-fab:report:'
    this.screenshotPrefix = opts.screenshotKeyPrefix ?? 'bug-fab/screenshots/'
    this.counterKey = opts.counterKey ?? 'bug-fab:counter'
  }

  private metaKey(id: string): string {
    return `${this.metaPrefix}${id}`
  }

  private screenshotKey(id: string): string {
    return `${this.screenshotPrefix}${id}.png`
  }

  /**
   * Allocate the next sequential ID. NOTE: KV is eventually consistent
   * across regions, so under heavy concurrent intake there is a race
   * window where two writers may pick the same number. Production
   * deployments should consider Durable Objects or D1 for strict
   * monotonic IDs. For the v0.1 conformance suite (single-tenant) this
   * is acceptable.
   */
  private async nextId(): Promise<string> {
    const raw = await this.kv.get(this.counterKey)
    const next = (raw === null ? 0 : Number(raw)) + 1
    await this.kv.put(this.counterKey, String(next))
    const padded = String(next).padStart(3, '0')
    return `bug-${this.idPrefix}${padded}`
  }

  async saveReport(meta: StoredMetadata, screenshotBytes: Uint8Array): Promise<string> {
    const id = await this.nextId()
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
      lifecycle: [{ action: 'created', by: 'anonymous', at: now, status: 'open' }],
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

    // Persist metadata first; if R2 write fails we can still surface
    // the report (with `has_screenshot: false` in a future revision).
    await this.kv.put(this.metaKey(id), JSON.stringify({ ...detail, _archived: false }))
    await this.bucket.put(this.screenshotKey(id), screenshotBytes)
    return id
  }

  private async readDetail(id: string): Promise<{ detail: BugReportDetail; archived: boolean } | null> {
    const raw = await this.kv.get(this.metaKey(id))
    if (raw === null) return null
    const parsed = JSON.parse(raw) as BugReportDetail & { _archived?: boolean }
    const archived = Boolean(parsed._archived)
    delete (parsed as Partial<BugReportDetail> & { _archived?: boolean })._archived
    return { detail: parsed as BugReportDetail, archived }
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    const r = await this.readDetail(id)
    return r?.detail ?? null
  }

  async listReports(
    filters: ListFilters,
    page: number,
    pageSize: number,
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }> {
    // KV does not support filter-server-side queries — page through all
    // keys, filter in memory. Fine for hobby-scale collectors; for
    // larger volumes, swap to D1 or an external SQL store via a
    // different IStorage implementation.
    const all: BugReportDetail[] = []
    let cursor: string | undefined
    do {
      const listing = await this.kv.list({
        prefix: this.metaPrefix,
        cursor,
        limit: 1000,
      })
      for (const k of listing.keys) {
        const r = await this.readDetail(k.name.slice(this.metaPrefix.length))
        if (!r) continue
        if (!filters.include_archived && r.archived) continue
        if (filters.status && r.detail.status !== filters.status) continue
        if (filters.severity && r.detail.severity !== filters.severity) continue
        if (filters.environment && r.detail.environment !== filters.environment) continue
        all.push(r.detail)
      }
      cursor = listing.list_complete ? undefined : listing.cursor
    } while (cursor)

    all.sort((a, b) => b.created_at.localeCompare(a.created_at))

    const stats: BugReportListStats = { open: 0, investigating: 0, fixed: 0, closed: 0 }
    for (const d of all) {
      const s = d.status
      if (s === 'open' || s === 'investigating' || s === 'fixed' || s === 'closed') stats[s]++
    }

    const total = all.length
    const start = Math.max(0, (page - 1) * pageSize)
    const slice = all.slice(start, start + pageSize)

    const items: BugReportSummary[] = slice.map((d) => ({
      id: d.id,
      title: d.title,
      report_type: d.report_type,
      severity: d.severity,
      status: d.status,
      module: d.module,
      created_at: d.created_at,
      has_screenshot: d.has_screenshot,
      github_issue_url: d.github_issue_url,
    }))

    return { items, total, stats }
  }

  async getScreenshotBytes(id: string): Promise<Uint8Array | null> {
    const obj = await this.bucket.get(this.screenshotKey(id))
    if (!obj) return null
    const ab = await obj.arrayBuffer()
    return new Uint8Array(ab)
  }

  async updateStatus(
    id: string,
    newStatus: Status,
    by: string,
    fixCommit?: string,
    fixDescription?: string,
  ): Promise<BugReportDetail> {
    const r = await this.readDetail(id)
    if (!r) throw new Error(`report ${id} not found`)
    const at = new Date().toISOString()
    r.detail.status = newStatus
    r.detail.updated_at = at
    const event: LifecycleEvent = { action: 'status_changed', by, at, status: newStatus }
    if (fixCommit) event.fix_commit = fixCommit
    if (fixDescription) event.fix_description = fixDescription
    r.detail.lifecycle.push(event)
    await this.kv.put(this.metaKey(id), JSON.stringify({ ...r.detail, _archived: r.archived }))
    return r.detail
  }

  async deleteReport(id: string): Promise<void> {
    const r = await this.readDetail(id)
    if (!r) throw new Error(`report ${id} not found`)
    await this.kv.delete(this.metaKey(id))
    await this.bucket.delete(this.screenshotKey(id))
  }

  async archiveReport(id: string): Promise<void> {
    const r = await this.readDetail(id)
    if (!r) throw new Error(`report ${id} not found`)
    const at = new Date().toISOString()
    r.detail.updated_at = at
    r.detail.lifecycle.push({ action: 'archived', by: null, at })
    await this.kv.put(this.metaKey(id), JSON.stringify({ ...r.detail, _archived: true }))
  }

  async bulkCloseFixed(): Promise<number> {
    let n = 0
    let cursor: string | undefined
    do {
      const listing = await this.kv.list({ prefix: this.metaPrefix, cursor, limit: 1000 })
      for (const k of listing.keys) {
        const id = k.name.slice(this.metaPrefix.length)
        const r = await this.readDetail(id)
        if (!r || r.detail.status !== 'fixed') continue
        const at = new Date().toISOString()
        r.detail.status = 'closed'
        r.detail.updated_at = at
        r.detail.lifecycle.push({ action: 'status_changed', by: null, at, status: 'closed' })
        await this.kv.put(this.metaKey(id), JSON.stringify({ ...r.detail, _archived: r.archived }))
        n++
      }
      cursor = listing.list_complete ? undefined : listing.cursor
    } while (cursor)
    return n
  }

  async bulkArchiveClosed(): Promise<number> {
    let n = 0
    let cursor: string | undefined
    do {
      const listing = await this.kv.list({ prefix: this.metaPrefix, cursor, limit: 1000 })
      for (const k of listing.keys) {
        const id = k.name.slice(this.metaPrefix.length)
        const r = await this.readDetail(id)
        if (!r || r.detail.status !== 'closed' || r.archived) continue
        const at = new Date().toISOString()
        r.detail.updated_at = at
        r.detail.lifecycle.push({ action: 'archived', by: null, at })
        await this.kv.put(this.metaKey(id), JSON.stringify({ ...r.detail, _archived: true }))
        n++
      }
      cursor = listing.list_complete ? undefined : listing.cursor
    } while (cursor)
    return n
  }

  async setGitHubIssue(id: string, issueUrl: string, issueNumber: number): Promise<void> {
    const r = await this.readDetail(id)
    if (!r) return
    r.detail.github_issue_url = issueUrl
    r.detail.github_issue_number = issueNumber
    await this.kv.put(this.metaKey(id), JSON.stringify({ ...r.detail, _archived: r.archived }))
  }
}
