// Viewer routes — list, detail, screenshot, status update, delete, bulk ops.
//
// All seven non-intake endpoints from PROTOCOL.md live here. The HTML
// list page (`GET /`) and detail page (`GET /:id/view`) sit alongside
// the JSON API endpoints; the consumer mounts the whole sub-app under
// a non-empty prefix (default `/admin/bug-reports`).
//
// IMPORTANT: do NOT import `node:fs` here. Edge runtimes (Cloudflare
// Workers, Vercel Edge, Deno Deploy) reject `node:` imports at deploy
// time. Screenshot bytes come from `storage.getScreenshotBytes(id)`
// directly — no filesystem path lookup.

import { Hono } from 'hono'
import type {
  IStorage,
  Status,
  Severity,
  ListFilters,
  BugFabAppOptions,
  BugFabViewerPermissions,
} from './types.js'
import { Errors } from './errors.js'
import {
  validateStatusUpdate,
  isValidStatus,
  isValidSeverity,
  isValidReportId,
  VALID_STATUSES,
  VALID_SEVERITIES,
} from './validation.js'
import { syncGitHubIssueState } from './github.js'
import { renderListPage, renderDetailPage } from './viewer-html/render.js'

const DEFAULT_PERMISSIONS: BugFabViewerPermissions = {
  can_edit_status: true,
  can_delete: true,
  can_bulk: true,
}

function effectivePermissions(opts: BugFabAppOptions): BugFabViewerPermissions {
  return { ...DEFAULT_PERMISSIONS, ...(opts.viewerPermissions ?? {}) }
}

export interface BuildViewerOptions {
  /** URL where the bundle is served, e.g. `/static/bug-fab.js`. */
  bundleUrl: string
}

/**
 * Build the viewer sub-app plus a standalone copy of the list-page
 * handler. The parent app needs the standalone handler so it can wire
 * the trailing-slash variant of the viewer root directly: Hono v4
 * collapses both `/` and `''` sub-app paths to `${prefix}` (no trailing
 * slash) when composed via `app.route()`, so `GET ${viewerPrefix}/`
 * otherwise 404s.
 */
export function buildViewerApp(
  opts: BugFabAppOptions,
  buildOpts: BuildViewerOptions = { bundleUrl: '/static/bug-fab.js' },
): { app: Hono; listHandler: (c: import('hono').Context) => Promise<Response> } {
  const viewer = new Hono()
  const storage: IStorage = opts.storage
  const perms = effectivePermissions(opts)

  // -------- HTML viewer pages --------

  // GET / — HTML list (root of the viewer prefix).
  const listHandler = async (c: import('hono').Context) => {
    const q = c.req.query()
    const page = Math.max(1, parseInt(q['page'] ?? '1', 10) || 1)
    const pageSize = Math.min(200, Math.max(1, parseInt(q['page_size'] ?? '20', 10) || 20))

    const filters: ListFilters = {
      include_archived: q['include_archived'] === 'true',
    }
    if (q['status'] && isValidStatus(q['status'])) filters.status = q['status'] as Status
    if (q['severity'] && isValidSeverity(q['severity'])) {
      filters.severity = q['severity'] as Severity
    }
    if (q['environment']) filters.environment = q['environment']

    const { items, total, stats } = await storage.listReports(filters, page, pageSize)
    const cspNonce = opts.cspNonce ? safeNonce(opts.cspNonce, c.req.raw) : null

    const html = renderListPage({
      items,
      total,
      page,
      pageSize,
      stats,
      bundleUrl: buildOpts.bundleUrl,
      detailUrlBase: '.', // row links resolve to `${viewerPrefix}/{id}/view`
      cspNonce,
    })
    return c.html(html)
  }
  viewer.get('/', listHandler)

  // GET /:id/view — HTML detail page.
  viewer.get('/:id/view', async (c) => {
    const id = c.req.param('id')
    if (!isValidReportId(id)) return c.json(Errors.notFound(`Report "${id}"`), 404)
    const report = await storage.getReport(id)
    if (!report) return c.json(Errors.notFound(`Report "${id}"`), 404)
    const cspNonce = opts.cspNonce ? safeNonce(opts.cspNonce, c.req.raw) : null
    const html = renderDetailPage({
      report,
      bundleUrl: buildOpts.bundleUrl,
      screenshotUrl: `../reports/${id}/screenshot`,
      listUrl: '..',
      cspNonce,
    })
    return c.html(html)
  })

  // -------- JSON API endpoints (the seven non-intake routes) --------

  // GET /reports — list with filters & pagination.
  viewer.get('/reports', async (c) => {
    const q = c.req.query()
    const page = Math.max(1, parseInt(q['page'] ?? '1', 10) || 1)
    const pageSize = Math.min(200, Math.max(1, parseInt(q['page_size'] ?? '20', 10) || 20))

    const filters: ListFilters = {
      include_archived: q['include_archived'] === 'true',
    }

    if (q['status']) {
      if (!isValidStatus(q['status'])) {
        return c.json(
          Errors.schemaError(
            `status must be one of: ${VALID_STATUSES.join(', ')}. Got: "${q['status']}"`,
          ),
          422,
        )
      }
      filters.status = q['status'] as Status
    }
    if (q['severity']) {
      if (!isValidSeverity(q['severity'])) {
        return c.json(
          Errors.schemaError(
            `severity must be one of: ${VALID_SEVERITIES.join(', ')}. Got: "${q['severity']}"`,
          ),
          422,
        )
      }
      filters.severity = q['severity'] as Severity
    }
    if (q['environment']) filters.environment = q['environment']

    try {
      const { items, total, stats } = await storage.listReports(filters, page, pageSize)
      return c.json({ items, total, page, page_size: pageSize, stats })
    } catch (err) {
      console.error('[bug-fab] listReports failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  // GET /reports/:id — single report detail.
  viewer.get('/reports/:id', async (c) => {
    const id = c.req.param('id')
    if (!isValidReportId(id)) return c.json(Errors.notFound(`Report "${id}"`), 404)
    try {
      const report = await storage.getReport(id)
      if (!report) return c.json(Errors.notFound(`Report "${id}"`), 404)
      return c.json(report)
    } catch (err) {
      console.error('[bug-fab] getReport failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  // GET /reports/:id/screenshot — raw PNG bytes.
  viewer.get('/reports/:id/screenshot', async (c) => {
    const id = c.req.param('id')
    if (!isValidReportId(id)) return c.json(Errors.notFound(`Screenshot for "${id}"`), 404)
    try {
      const bytes = await storage.getScreenshotBytes(id)
      if (!bytes) return c.json(Errors.notFound(`Screenshot for "${id}"`), 404)
      // Hono's c.body() accepts ArrayBufferLike; the Uint8Array view
      // is sliced to its precise byteLength so the receiver doesn't
      // see padding from a backing buffer larger than the PNG.
      const ab = bytes.buffer.slice(
        bytes.byteOffset,
        bytes.byteOffset + bytes.byteLength,
      ) as ArrayBuffer
      return c.body(ab, 200, {
        'Content-Type': 'image/png',
        'Content-Length': String(bytes.byteLength),
      })
    } catch (err) {
      console.error('[bug-fab] getScreenshot failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  // PUT /reports/:id/status — workflow status update.
  viewer.put('/reports/:id/status', async (c) => {
    if (!perms.can_edit_status) {
      return c.json(Errors.forbidden('can_edit_status'), 403)
    }
    const id = c.req.param('id')
    if (!isValidReportId(id)) return c.json(Errors.notFound(`Report "${id}"`), 404)

    let body: unknown
    try {
      body = await c.req.json()
    } catch {
      return c.json(Errors.validationError('Request body is required and must be JSON.'), 400)
    }

    const result = validateStatusUpdate(body)
    if (!result.ok) {
      return c.json(Errors.schemaError(result.errors.join('; ')), 422)
    }

    const obj = body as {
      status: Status
      fix_commit?: string
      fix_description?: string
    }
    const by = 'api' // No auth abstraction in v0.1; consumer handles auth at mount level.

    try {
      const existing = await storage.getReport(id)
      if (!existing) return c.json(Errors.notFound(`Report "${id}"`), 404)

      // Idempotent no-op — stored.status already matches request.status.
      // Adapter authors may also append a lifecycle entry on no-op; either
      // is conformant in v0.1. We choose the no-op-collapse semantics.
      if (existing.status === obj.status) {
        return c.json(existing)
      }

      const updated = await storage.updateStatus(
        id,
        obj.status,
        by,
        obj.fix_commit,
        obj.fix_description,
      )

      // Best-effort GitHub state sync — failure logs but doesn't fail.
      if (
        opts.github?.enabled &&
        opts.github.pat &&
        opts.github.repo &&
        updated.github_issue_number !== null &&
        updated.github_issue_number !== undefined
      ) {
        syncGitHubIssueState(
          { pat: opts.github.pat, repo: opts.github.repo, apiBase: opts.github.apiBase },
          updated.github_issue_number,
          obj.status,
          (msg) => console.warn(msg),
        ).catch((err) => console.warn(`[bug-fab] github state sync failed: ${String(err)}`))
      }

      return c.json(updated)
    } catch (err) {
      console.error('[bug-fab] updateStatus failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  // DELETE /reports/:id — hard delete.
  viewer.delete('/reports/:id', async (c) => {
    if (!perms.can_delete) return c.json(Errors.forbidden('can_delete'), 403)
    const id = c.req.param('id')
    if (!isValidReportId(id)) return c.json(Errors.notFound(`Report "${id}"`), 404)
    try {
      const existing = await storage.getReport(id)
      if (!existing) return c.json(Errors.notFound(`Report "${id}"`), 404)
      await storage.deleteReport(id)
      // Hono's c.body(null, 204) emits a true empty 204 with no Content-Type.
      return c.body(null, 204)
    } catch (err) {
      console.error('[bug-fab] deleteReport failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  // POST /bulk-close-fixed — transition all `fixed` → `closed`.
  viewer.post('/bulk-close-fixed', async (c) => {
    if (!perms.can_bulk) return c.json(Errors.forbidden('can_bulk'), 403)
    try {
      const closed = await storage.bulkCloseFixed()
      return c.json({ closed })
    } catch (err) {
      console.error('[bug-fab] bulkCloseFixed failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  // POST /bulk-archive-closed — move all `closed` to archive.
  viewer.post('/bulk-archive-closed', async (c) => {
    if (!perms.can_bulk) return c.json(Errors.forbidden('can_bulk'), 403)
    try {
      const archived = await storage.bulkArchiveClosed()
      return c.json({ archived })
    } catch (err) {
      console.error('[bug-fab] bulkArchiveClosed failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  return { app: viewer, listHandler }
}

/** Call the consumer's nonce provider with try/catch so a misbehaving
 *  provider can't crash a viewer page render. Falls back to `null`. */
function safeNonce(provider: (req: Request) => string | null, req: Request): string | null {
  try {
    const v = provider(req)
    return typeof v === 'string' && v.length > 0 ? v : null
  } catch {
    return null
  }
}
