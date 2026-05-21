// Bug-Fab Hono adapter conformance entry-point.
//
// Boots the adapter on port 8080 using MemoryStorage, mounting intake at
// /api and viewer at /admin/bug-reports — the same prefixes the
// reference bun-server example uses. Run via `bun run boot.ts` inside
// the oven/bun:1 container; Bun resolves the TypeScript imports natively
// so no build step is required.
//
// The conformance suite expects:
//   --base-url=http://<host>:8080/api
//   --viewer-base-url=http://<host>:8080/admin/bug-reports

import { createBugFabApp, MemoryStorage } from '../src/index.ts'

const storage = new MemoryStorage()

const app = createBugFabApp({
  storage,
  submitPrefix: '/api',
  viewerPrefix: '/admin/bug-reports',
})

const port = Number(process.env.PORT ?? '8080')

declare const Bun: { serve: (opts: { port: number; fetch: typeof app.fetch }) => unknown }

Bun.serve({ port, fetch: app.fetch })

// stderr so logs flush eagerly under Docker's default buffering.
console.error(`[bug-fab-hono conformance] listening on http://0.0.0.0:${port}`)
console.error(`  Intake:  POST /api/bug-reports`)
console.error(`  Viewer:  GET  /admin/bug-reports/`)
