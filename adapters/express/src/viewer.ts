// Bug-Fab viewer routes — list, detail, screenshot, status PUT, delete, bulk ops.
//
// References:
//   https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md
//   https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json
//
// Mount-prefix invariant: the list HTML page is served at GET "" (the
// router's mount point root). Document the canonical mount as
// `app.use("/admin/bug-reports", router)`.
//
// Mount-path resolution: HTML viewer routes capture `req.baseUrl` and pass
// it into the templates so rendered `<a href>` / `<img src>` attributes
// carry absolute paths under whatever prefix the router was mounted at.
// Express sets `req.baseUrl` to the mount path that matched the router
// (e.g. `/admin/bug-reports` for `app.use('/admin/bug-reports', router)`),
// which is exactly what the templates need. Do not hard-code a mount
// constant here — relying on a static value re-introduces the broken-link
// bug fixed in audit 2026-05-01_express_adapter Drift C.
//
// Reverse-proxy caveat: `req.baseUrl` reflects the Express-internal mount,
// not the externally visible URL. If the consumer terminates TLS at a
// proxy that strips a prefix, they should set `app.set('trust proxy', ...)`
// and may need to read `X-Forwarded-Prefix` to reconstruct the public
// path. README "Common pitfalls" covers this.

import { createReadStream, existsSync, statSync } from 'node:fs'
import express, { type Request, type Response, type Router } from 'express'

import { Errors } from './errors.js'
import {
  isValidStatus, isValidSeverity, validateStatusUpdate,
  VALID_STATUSES, VALID_SEVERITIES,
} from './validation.js'
import type {
  BugFabRouterOptions, IStorage, Logger, ListFilters, Severity, Status,
} from './types.js'
import { renderListPage, renderDetailPage } from './templates/index.js'
import { syncGitHubIssueState } from './github.js'

export function registerViewerRoutes(
  router:  Router,
  storage: IStorage,
  options: BugFabRouterOptions,
  log:     Logger,
): void {
  const perms = options.viewerPermissions ?? {}
  const canEditStatus = perms.can_edit_status !== false
  const canDelete     = perms.can_delete !== false
  const canBulk       = perms.can_bulk !== false

  // ----- GET "" — HTML list page -----
  //
  // The protocol uses /reports for the JSON list endpoint. The mount-point
  // root is reserved for the HTML viewer page. Consumers calling fetch()
  // should always use /reports, never the bare prefix.
  router.get('/', async (req: Request, res: Response): Promise<void> => {
    try {
      const { items, total, stats } = await storage.listReports({}, 1, 50)
      const html = renderListPage({
        mountPath: req.baseUrl,
        items,
        stats,
        total,
        page:     1,
        pageSize: 50,
      })
      res.status(200).type('html').send(html)
    } catch (err) {
      log.error('[bug-fab-express] viewer list HTML failed', err)
      res.status(503).json(Errors.storageUnavailable())
    }
  })

  // ----- GET /reports — paginated JSON list -----
  router.get('/reports', async (req: Request, res: Response): Promise<void> => {
    const q = req.query as Record<string, string | undefined>

    const page     = Math.max(1, parseInt(q.page ?? '1', 10) || 1)
    const pageSize = Math.min(200, Math.max(1, parseInt(q.page_size ?? '20', 10) || 20))

    const filters: ListFilters = {
      include_archived: q.include_archived === 'true',
    }

    if (q.status !== undefined) {
      if (!isValidStatus(q.status)) {
        res.status(422).json(Errors.schemaError(
          `status must be one of: ${VALID_STATUSES.join(', ')}. Got: "${q.status}"`,
        ))
        return
      }
      filters.status = q.status as Status
    }
    if (q.severity !== undefined) {
      if (!isValidSeverity(q.severity)) {
        res.status(422).json(Errors.schemaError(
          `severity must be one of: ${VALID_SEVERITIES.join(', ')}. Got: "${q.severity}"`,
        ))
        return
      }
      filters.severity = q.severity as Severity
    }
    if (q.environment !== undefined) {
      filters.environment = q.environment
    }

    try {
      const { items, total, stats } = await storage.listReports(filters, page, pageSize)
      res.status(200).json({ items, total, page, page_size: pageSize, stats })
    } catch (err) {
      log.error('[bug-fab-express] listReports failed', err)
      res.status(503).json(Errors.storageUnavailable())
    }
  })

  // ----- GET /reports/:id — JSON detail OR HTML detail -----
  //
  // Negotiates on Accept: HTML browsers get the templated detail page,
  // JSON consumers (fetch / curl) get the protocol shape.
  router.get('/reports/:id', async (req: Request, res: Response): Promise<void> => {
    const id = req.params.id
    if (!id) {
      res.status(404).json(Errors.notFound('report'))
      return
    }

    try {
      const detail = await storage.getReport(id)
      if (!detail) {
        res.status(404).json(Errors.notFound(`report "${id}"`))
        return
      }

      const accept = req.header('accept') ?? ''
      // If the browser is unambiguous about wanting HTML, render the page.
      // JSON-first consumers should send Accept: application/json or omit it.
      if (accept.includes('text/html')) {
        const html = renderDetailPage({ mountPath: req.baseUrl, detail })
        res.status(200).type('html').send(html)
      } else {
        res.status(200).json(detail)
      }
    } catch (err) {
      log.error(`[bug-fab-express] getReport(${id}) failed`, err)
      res.status(503).json(Errors.storageUnavailable())
    }
  })

  // ----- GET /reports/:id/screenshot -----
  router.get('/reports/:id/screenshot', async (req: Request, res: Response): Promise<void> => {
    const id = req.params.id
    if (!id) {
      res.status(404).json(Errors.notFound('screenshot'))
      return
    }

    try {
      const path = await storage.getScreenshotPath(id)
      if (!path || !existsSync(path)) {
        res.status(404).json(Errors.notFound(`screenshot for "${id}"`))
        return
      }
      const stat = statSync(path)
      res.status(200)
      res.setHeader('Content-Type',   'image/png')
      res.setHeader('Content-Length', String(stat.size))
      createReadStream(path).pipe(res)
    } catch (err) {
      log.error(`[bug-fab-express] getScreenshotPath(${id}) failed`, err)
      res.status(503).json(Errors.storageUnavailable())
    }
  })

  // ----- PUT /reports/:id/status -----
  //
  // express.json() is mounted **per-route** here so it does not conflict
  // with multer on the intake route. Mounting express.json() globally is
  // the most common Express + multer pitfall — see README.
  if (canEditStatus) {
    router.put(
      '/reports/:id/status',
      express.json({ limit: '64kb' }),
      async (req: Request, res: Response): Promise<void> => {
        const id = req.params.id
        if (!id) {
          res.status(404).json(Errors.notFound('report'))
          return
        }

        const validation = validateStatusUpdate(req.body)
        if (!validation.ok) {
          res.status(422).json(Errors.schemaError(validation.errors.join('; ')))
          return
        }

        const body = req.body as { status: Status; fix_commit?: string; fix_description?: string }

        try {
          const current = await storage.getReport(id)
          if (!current) {
            res.status(404).json(Errors.notFound(`report "${id}"`))
            return
          }

          // Idempotent no-op: same status, return current detail unchanged.
          if (current.status === body.status) {
            res.status(200).json(current)
            return
          }

          const updated = await storage.updateStatus(
            id,
            body.status,
            'api',
            body.fix_commit,
            body.fix_description,
          )

          // Best-effort GitHub state sync (close on fixed/closed, reopen otherwise).
          if (
            options.github?.enabled
            && options.github.pat
            && options.github.repo
            && current.github_issue_number != null
          ) {
            await syncGitHubIssueState(
              {
                pat:     options.github.pat,
                repo:    options.github.repo,
                apiBase: options.github.apiBase,
              },
              current.github_issue_number,
              body.status,
              log,
            )
          }

          res.status(200).json(updated)
        } catch (err) {
          log.error(`[bug-fab-express] updateStatus(${id}) failed`, err)
          res.status(503).json(Errors.storageUnavailable())
        }
      },
    )
  }

  // ----- DELETE /reports/:id -----
  if (canDelete) {
    router.delete('/reports/:id', async (req: Request, res: Response): Promise<void> => {
      const id = req.params.id
      if (!id) {
        res.status(404).json(Errors.notFound('report'))
        return
      }

      try {
        const current = await storage.getReport(id)
        if (!current) {
          res.status(404).json(Errors.notFound(`report "${id}"`))
          return
        }
        await storage.deleteReport(id)
        res.status(204).end()
      } catch (err) {
        log.error(`[bug-fab-express] deleteReport(${id}) failed`, err)
        res.status(503).json(Errors.storageUnavailable())
      }
    })
  }

  // ----- POST /bulk-close-fixed -----
  if (canBulk) {
    router.post('/bulk-close-fixed', async (_req: Request, res: Response): Promise<void> => {
      try {
        const closed = await storage.bulkCloseFixed()
        res.status(200).json({ closed })
      } catch (err) {
        log.error('[bug-fab-express] bulkCloseFixed failed', err)
        res.status(503).json(Errors.storageUnavailable())
      }
    })

    // ----- POST /bulk-archive-closed -----
    router.post('/bulk-archive-closed', async (_req: Request, res: Response): Promise<void> => {
      try {
        const archived = await storage.bulkArchiveClosed()
        res.status(200).json({ archived })
      } catch (err) {
        log.error('[bug-fab-express] bulkArchiveClosed failed', err)
        res.status(503).json(Errors.storageUnavailable())
      }
    })
  }
}
