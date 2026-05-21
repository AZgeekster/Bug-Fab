// Cloudflare KV-only storage backend.
//
// All metadata AND screenshots stored as KV values (screenshots are
// base64-encoded into KV due to the 25 MiB per-value limit). Use this
// when you don't have an R2 bucket provisioned but still want a quick
// edge-runtime POC. For anything bigger than ~5 MiB screenshots,
// `R2Storage` is the right choice.
//
// This class wraps R2Storage's logic but persists screenshots as
// base64 strings inside KV. The IStorage interface is identical so
// consumers can swap implementations transparently.

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

export interface KVStorageOptions {
  kv: KVNamespace
  idPrefix?: string
  metadataKeyPrefix?: string
  screenshotKeyPrefix?: string
  counterKey?: string
}

function bytesToBase64(bytes: Uint8Array): string {
  let s = ''
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i] as number)
  // `btoa` is available across Workers / Bun / Deno / Vercel Edge / Node 18+.
  return btoa(s)
}

function base64ToBytes(b64: string): Uint8Array {
  const s = atob(b64)
  const out = new Uint8Array(s.length)
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i)
  return out
}

export class KVStorage implements IStorage {
  private readonly kv: KVNamespace
  private readonly idPrefix: string
  private readonly metaPrefix: string
  private readonly screenshotPrefix: string
  private readonly counterKey: string

  constructor(opts: KVStorageOptions) {
    this.kv = opts.kv
    this.idPrefix = opts.idPrefix ?? ''
    this.metaPrefix = opts.metadataKeyPrefix ?? 'bug-fab:report:'
    this.screenshotPrefix = opts.screenshotKeyPrefix ?? 'bug-fab:screenshot:'
    this.counterKey = opts.counterKey ?? 'bug-fab:counter'
  }

  private metaKey(id: string): string {
    return `${this.metaPrefix}${id}`
  }

  private screenshotKey(id: string): string {
    return `${this.screenshotPrefix}${id}`
  }

  private async nextId(): Promise<string> {
    const raw = await this.kv.get(this.counterKey)
    const next = (raw === null ? 0 : Number(raw)) + 1
    await this.kv.put(this.counterKey, String(next))
    return `bug-${this.idPrefix}${String(next).padStart(3, '0')}`
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

    await this.kv.put(this.metaKey(id), JSON.stringify({ ...detail, _archived: false }))
    await this.kv.put(this.screenshotKey(id), bytesToBase64(screenshotBytes))
    return id
  }

  private async readDetail(
    id: string,
  ): Promise<{ detail: BugReportDetail; archived: boolean } | null> {
    const raw = await this.kv.get(this.metaKey(id))
    if (raw === null) return null
    const parsed = JSON.parse(raw) as BugReportDetail & { _archived?: boolean }
    const archived = Boolean(parsed._archived)
    delete (parsed as Partial<BugReportDetail> & { _archived?: boolean })._archived
    return { detail: parsed as BugReportDetail, archived }
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    return (await this.readDetail(id))?.detail ?? null
  }

  async listReports(
    filters: ListFilters,
    page: number,
    pageSize: number,
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }> {
    const all: BugReportDetail[] = []
    let cursor: string | undefined
    do {
      const listing = await this.kv.list({ prefix: this.metaPrefix, cursor, limit: 1000 })
      for (const k of listing.keys) {
        const id = k.name.slice(this.metaPrefix.length)
        const r = await this.readDetail(id)
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
    const b64 = await this.kv.get(this.screenshotKey(id))
    if (b64 === null) return null
    return base64ToBytes(b64)
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
    await this.kv.delete(this.screenshotKey(id))
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
        await this.kv.put(
          this.metaKey(id),
          JSON.stringify({ ...r.detail, _archived: r.archived }),
        )
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
