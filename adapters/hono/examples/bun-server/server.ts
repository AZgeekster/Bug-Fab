// Bun.serve example — runs on `bun run server.ts`.
//
// Uses the in-memory storage backend so this example needs no external
// services. Real Bun deployments should swap in a custom IStorage
// backed by `bun:sqlite` or `node:fs` (per src/storage/README.md).

import { createBugFabApp, MemoryStorage } from 'bug-fab-hono'

const storage = new MemoryStorage()

const app = createBugFabApp({
  storage,
  submitPrefix: '/api',
  viewerPrefix: '/admin/bug-reports',
})

const port = Number(process.env.PORT ?? '3000')

// `Bun` is the global injected by the Bun runtime. Vitest doesn't need
// this file; it's only loaded under `bun run`. Suppressing the
// `Bun is not defined` complaint with a runtime check keeps the
// example self-contained.
declare const Bun: { serve: (opts: { port: number; fetch: typeof app.fetch }) => unknown }

if (typeof Bun !== 'undefined') {
  Bun.serve({ port, fetch: app.fetch })
  console.log(`Bug-Fab listening on http://localhost:${port}`)
  console.log(`  Intake:  POST /api/bug-reports`)
  console.log(`  Viewer:  GET  /admin/bug-reports/`)
}
