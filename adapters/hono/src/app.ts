// `createBugFabApp(opts)` — public entry point.
//
// Returns a fresh `Hono` instance with all 8 protocol endpoints wired
// up. The intake router is mounted under `submitPrefix` (default
// `/api`) and the viewer router under `viewerPrefix` (default
// `/admin/bug-reports`). Mount this app inside the consumer's existing
// Hono server (`app.route('/', createBugFabApp({ storage }))`) or use
// it directly as the root handler in a Worker.
//
// Hono is itself a fetch-style handler, so the same instance runs
// unchanged on Cloudflare Workers, Bun.serve, Deno.serve, and Vercel
// Edge. See examples/ for runtime-specific glue.

import { Hono } from 'hono'
import type { BugFabAppOptions } from './types.js'
import { Errors } from './errors.js'
import { buildIntakeApp } from './intake.js'
import { buildViewerApp, type BuildViewerOptions } from './viewer.js'

export interface CreateBugFabAppExtraOptions extends BuildViewerOptions {
  /** Set to false to skip the package's onError handler (advanced use). */
  installErrorHandler?: boolean
}

export function createBugFabApp(
  opts: BugFabAppOptions,
  extra: Partial<CreateBugFabAppExtraOptions> = {},
): Hono {
  if (!opts.storage) {
    throw new Error('[bug-fab] opts.storage is required')
  }

  const submitPrefix = opts.submitPrefix ?? '/api'
  const viewerPrefix = opts.viewerPrefix ?? '/admin/bug-reports'

  if (!viewerPrefix || viewerPrefix === '/') {
    throw new Error(
      '[bug-fab] viewerPrefix must be a non-empty, non-root path. ' +
        'The viewer serves an HTML list at its root — mounting at "/" collapses it ' +
        'with your app root. Use "/admin", "/admin/bug-reports", or similar.',
    )
  }

  const app = new Hono()

  // Mount the two routers. Hono.route() is composition-friendly: the
  // sub-app's routes inherit the prefix, so `intake.post('/bug-reports')`
  // becomes `POST {submitPrefix}/bug-reports` at the parent level.
  app.route(submitPrefix, buildIntakeApp(opts))
  const { app: viewerApp, listHandler } = buildViewerApp(opts, {
    bundleUrl: extra.bundleUrl ?? '/static/bug-fab.js',
  })
  app.route(viewerPrefix, viewerApp)

  // Hono v4 collapses both `/` and `''` sub-app paths to `${prefix}` when
  // composed via `app.route()`, so the trailing-slash variant of the
  // viewer root (`GET ${viewerPrefix}/`) would otherwise 404. Wire the
  // list handler on the parent at the exact trailing-slash path so a
  // direct browser visit to e.g. `/admin/bug-reports/` resolves cleanly
  // without a 301 redirect.
  app.get(`${viewerPrefix}/`, listHandler)

  // Preserve the protocol error envelope. Hono's default error path
  // emits a plain-text `Internal Server Error`, which would corrupt the
  // `{error, detail}` JSON shape and fail the conformance suite.
  if (extra.installErrorHandler !== false) {
    app.onError((err, c) => {
      console.error('[bug-fab] unhandled error:', err)
      return c.json(Errors.internalError(String(err.message ?? err)), 500)
    })
    // Same reasoning for unmatched routes: Hono's default 404 is a
    // plain-text `404 Not Found`. Wrap it in the protocol envelope so a
    // typo'd URL or a trailing-slash mismatch surfaces as a structured
    // JSON error rather than HTML the consumer's UI can't display.
    app.notFound((c) => c.json(Errors.notFound(c.req.path), 404))
  }

  return app
}

/**
 * Convenience helper: mount Bug-Fab onto an existing parent Hono app
 * and return the parent. Useful when you don't want a fresh root app.
 *
 *     mountBugFab(myApp, { storage })
 */
export function mountBugFab<E extends Record<string, unknown>>(
  parent: Hono<E>,
  opts: BugFabAppOptions,
  extra: Partial<CreateBugFabAppExtraOptions> = {},
): Hono<E> {
  const child = createBugFabApp(opts, extra)
  parent.route('/', child)
  return parent
}
