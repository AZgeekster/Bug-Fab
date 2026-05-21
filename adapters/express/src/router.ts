// `createBugFabRouter(opts)` — single Express router exposing the eight
// Bug-Fab v0.1 wire-protocol endpoints.
//
// Reference: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md
//            https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md
//
// Canonical mount: `app.use("/admin/bug-reports", createBugFabRouter({ storage }))`.
// The router serves an HTML list at the mount root; never mount at "/".

import express, { type Router } from 'express'
import {
  buildIntakeMulter, buildIntakeHandler, buildMulterErrorHandler,
} from './intake.js'
import { registerViewerRoutes } from './viewer.js'
import type { BugFabRouterOptions, Logger } from './types.js'
import { DEFAULT_MAX_SCREENSHOT_BYTES } from './validation.js'

const consoleLogger: Logger = {
  info: (msg, ...args) => console.info(`[bug-fab-express] ${msg}`, ...args),
  warn: (msg, ...args) => console.warn(`[bug-fab-express] ${msg}`, ...args),
  error: (msg, ...args) => console.error(`[bug-fab-express] ${msg}`, ...args),
}

export function createBugFabRouter(opts: BugFabRouterOptions): Router {
  if (!opts || !opts.storage) {
    throw new Error('[bug-fab-express] createBugFabRouter: opts.storage is required')
  }

  const log      = opts.logger ?? consoleLogger
  const maxBytes = opts.maxScreenshotBytes ?? DEFAULT_MAX_SCREENSHOT_BYTES

  const router: Router = express.Router()

  // ---- Intake ----
  // POST /bug-reports — multipart with metadata (text JSON) + screenshot (PNG).
  // Multer is configured PER-ROUTE; do not mount it on the whole router or
  // it will swallow the JSON status PUT body.
  const upload = buildIntakeMulter(maxBytes)
  router.post(
    '/bug-reports',
    upload.single('screenshot'),
    buildIntakeHandler(opts.storage, opts, log, maxBytes),
  )

  // Multer-specific error middleware — must be AFTER the intake route so
  // it catches errors thrown by upload.single (e.g. LIMIT_FILE_SIZE).
  router.use(buildMulterErrorHandler(maxBytes))

  // ---- Viewer ----
  // The viewer captures `req.baseUrl` per-request inside its HTML routes
  // and builds absolute template URLs from it, so consumers can mount the
  // router at any prefix (`/admin/bug-reports`, `/internal/bugs`, etc.)
  // and the rendered links resolve correctly. There is no construction-
  // time mount path on purpose: hard-coding it caused the broken-link
  // bug fixed in audit 2026-05-01_express_adapter Drift C.
  registerViewerRoutes(router, opts.storage, opts, log)

  return router
}
