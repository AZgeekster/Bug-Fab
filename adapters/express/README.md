# bug-fab-express

[Bug-Fab][bf] adapter for [Express][exp]. Exposes the eight v0.1 wire-protocol
endpoints as a single mountable `express.Router`, with a zero-dependency
filesystem storage backend out of the box.

> **Status:** first-party adapter (promoted 2026-05-21, v0.1.0). Tracks the
> [Bug-Fab v0.1 wire protocol][protocol]. Verified 41/41 tests passing
> (9 conformance + 18 viewer + 14 intake) on Node 20.

[bf]: https://github.com/AZgeekster/Bug-Fab
[exp]: https://expressjs.com/
[protocol]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md

## Why

Bug-Fab is a framework-agnostic in-app bug-reporting tool. The [reference
adapter is in Python (FastAPI)][fastapi]; this package lets Express apps
consume the same wire protocol so the [Bug-Fab frontend bundle][frontend]
drops in unchanged.

[fastapi]: https://github.com/AZgeekster/Bug-Fab
[frontend]: https://github.com/AZgeekster/Bug-Fab/tree/main/static

## Install

```bash
npm install bug-fab-express express multer
```

`express` and `multer` are peer dependencies — the adapter does not pin
them so you control the upgrade cadence.

Requires:

- Node.js >= 20 (uses native `fetch`)
- Express >= 4.18
- multer >= 1.4.5

## Quickstart

```ts
import express from 'express'
import { createBugFabRouter, FileStorage } from 'bug-fab-express'

const app = express()

// File-based storage. `storageDir` is created if it does not exist.
const storage = new FileStorage({ storageDir: './var/bug_fab' })

// Mount the eight endpoints under one prefix.
//
// IMPORTANT: do NOT mount at "/" — the router serves an HTML list at
// the prefix root. Mount under a distinct path like the example below.
//
// The viewer captures `req.baseUrl` per-request, so any non-root mount
// works: `/admin/bug-reports`, `/internal/bugs`, `/api/v2/internal/bugs`,
// etc. The HTML viewer's links and screenshot URLs are built relative
// to the actual mount, no construction-time configuration needed.
app.use('/admin/bug-reports', createBugFabRouter({ storage }))

app.listen(3000, () => {
  console.log('Bug-Fab running at http://localhost:3000/admin/bug-reports')
})
```

That's it. Open http://localhost:3000/admin/bug-reports in a browser to
see the (empty) viewer; POST a multipart submission to
`/admin/bug-reports/bug-reports` to file the first one.

## What you get

The eight protocol endpoints, mounted under your chosen prefix:

| Method   | Path                              | Purpose                              |
|----------|-----------------------------------|--------------------------------------|
| `POST`   | `/bug-reports`                    | Submit a report (multipart).         |
| `GET`    | `/`                               | HTML viewer (list page).             |
| `GET`    | `/reports`                        | JSON list, filterable.               |
| `GET`    | `/reports/:id`                    | JSON detail (or HTML if `Accept`).   |
| `GET`    | `/reports/:id/screenshot`         | Raw PNG.                             |
| `PUT`    | `/reports/:id/status`             | Update status, append lifecycle.     |
| `DELETE` | `/reports/:id`                    | Hard delete.                         |
| `POST`   | `/bulk-close-fixed`               | Close all fixed reports.             |
| `POST`   | `/bulk-archive-closed`            | Archive all closed reports.          |

All requests/responses use `snake_case` JSON keys verbatim from the wire.
Do **not** wire up a camelCase converter on these routes.

## Configuration

```ts
createBugFabRouter({
  // Required.
  storage,

  // Optional — best-effort GitHub Issues sync. Failures are logged and
  // never block intake; this is per protocol.
  github: {
    enabled: true,
    pat:     process.env.GITHUB_PAT!,
    repo:    'octocat/octocat-internal-tools',
  },

  // Optional — coarse permissions toggle. When set to false, the matching
  // routes are NOT registered (return 404 from Express's default handler).
  // v0.1 has no per-user auth; protect the mount with your own middleware.
  viewerPermissions: {
    can_edit_status: true,
    can_delete:      true,
    can_bulk:        true,
  },

  // Optional — single-process per-IP rate limiter. Disabled by default.
  // For PM2 / clustered deployments use a reverse-proxy limiter instead.
  rateLimit: {
    enabled:     true,
    maxRequests: 30,
    windowMs:    60_000,
  },

  // Optional — override the screenshot cap. Protocol cap is 10 MiB; you
  // MAY enforce stricter limits but not looser.
  maxScreenshotBytes: 5 * 1024 * 1024,

  // Optional — plug your own logger.
  logger: {
    info:  (msg, ...args) => myLog.info(msg, ...args),
    warn:  (msg, ...args) => myLog.warn(msg, ...args),
    error: (msg, ...args) => myLog.error(msg, ...args),
  },
})
```

## Auth — protect routes at the mount point

Bug-Fab v0.1 ships **no auth abstraction**. Pick a mount strategy that
matches your trust model:

```ts
// Public submit, admin-only viewer (most common):
app.post('/api/bug-reports/*', /* no auth */)
app.use('/api/bug-reports',    createBugFabRouter({ storage }))   // intake-only path
app.use('/admin/bug-reports',  requireAdmin, createBugFabRouter({ storage }))   // protected viewer

// Auth required for everything (internal tools):
app.use('/admin/bug-reports', requireAdmin, createBugFabRouter({ storage }))

// Wide-open POC:
app.use('/bug-reports', createBugFabRouter({ storage }))
```

In v0.2 a proper `AuthAdapter` ABC will let the adapter ask "who is logged
in?" so the lifecycle audit log can record server-derived identity instead
of `"anonymous"`. Until then, mount-point delegation is the contract.

## CSP guidance

The Bug-Fab frontend bundle's overlay uses inline `<script>` and `<style>`
nodes. If you serve a strict Content-Security-Policy, you have two options:

1. **`unsafe-inline`** for `script-src` and `style-src` on the page that
   embeds the bundle. Simple, less secure.
2. **Nonce-injection** — generate a per-request nonce in your CSP middleware
   (e.g. via [`helmet`][helmet]'s `contentSecurityPolicy.directives.scriptSrc`
   with a function), then have your template wire that nonce onto the
   bundle's `<script nonce="...">` tag.

This adapter does **not** generate nonces — that responsibility lives with
your CSP middleware. See the upstream [`docs/CSP.md`][csp] for the same
guidance applied to the Python reference; the principles are identical for
Express.

[helmet]: https://helmetjs.github.io/
[csp]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/CSP.md

## Storage backends

### `FileStorage` (default)

JSON-on-disk, atomic temp-file-then-rename writes, in-memory index for fast
filtering. Single-process safe; **not** safe for PM2 cluster mode or
multi-host deployments — they will race on the ID counter.

```ts
import { FileStorage } from 'bug-fab-express'

const storage = new FileStorage({
  storageDir: './var/bug_fab',
  idPrefix:   'P',   // optional — produces ids like bug-P001
})
```

Layout on disk:

```
var/bug_fab/
├── bug-001/
│   ├── metadata.json
│   └── screenshot.png
├── bug-002/...
└── archive/
    └── bug-007/...
```

### Custom backends

Implement the [`IStorage`][istorage] contract (9 methods + an optional
`setGitHubIssue` post-save hook). A typical SQLite or Postgres
implementation needs ~150 lines.

[istorage]: ./src/storage/IStorage.ts

```ts
import type { IStorage } from 'bug-fab-express'

class PostgresStorage implements IStorage {
  // saveReport, getReport, listReports, getScreenshotPath,
  // updateStatus, deleteReport, archiveReport,
  // bulkCloseFixed, bulkArchiveClosed
  // (Optional: setGitHubIssue)
}
```

## Common Express + Bug-Fab pitfalls

These are the traps that catch hand-rolled Express + multer bug reporters
and that this package handles for you — keep them in mind if you build
something similar from scratch:

- **`express.json()` mounted globally clobbers multer.** This adapter
  mounts `express.json()` per-route on `PUT /reports/:id/status` only. If
  your app already mounts `express.json()` globally, that's fine — the
  intake route uses multer's body buffer, not Express's parsed body.
- **`multer({ limits })` is per-route.** Setting it at app level forces
  every endpoint to pay the multipart-parsing cost. The adapter scopes
  the limit to the intake route alone.
- **`fileFilter` rejections become Express errors, not clean responses.**
  This adapter routes multer errors through a translation middleware so
  oversized files surface as `413 payload_too_large` (with `limit_bytes`
  in the body) and bad mimetypes as `415 unsupported_media_type`.
- **Magic-byte PNG check, not Content-Type sniffing.** A client can claim
  `image/png` while sending JPEG bytes — and html2canvas in some browsers
  emits `image/jpeg` despite producing PNG. The adapter checks the actual
  bytes (`89 50 4E 47 0D 0A 1A 0A`).
- **`fetch` in Node 18+ behaves differently from `node-fetch`.** GitHub
  sync uses native `fetch` and explicitly checks `response.ok`; do not
  expect a thrown error on non-2xx.
- **Snake_case across the wire.** All keys in request/response bodies are
  snake_case. TypeScript interfaces in this package preserve that — do
  not slap a camelCase plugin on Bug-Fab routes.
- **Reverse-proxy prefix stripping affects HTML viewer links.** The HTML
  viewer's `<a href>` and `<img src>` paths are built from `req.baseUrl`,
  which Express sets to the mount path that matched inside Express. If
  you terminate TLS at a proxy that strips a prefix before forwarding
  (e.g. nginx exposes `/bugs` externally but the upstream Express app is
  mounted at `/internal/bugs`), the rendered links use the upstream path,
  not the public one. Set `app.set('trust proxy', ...)` and consider
  reading `X-Forwarded-Prefix` if you need the public path. For
  same-origin mounts (no prefix rewrite), no action needed.

## Testing

```bash
npm test           # vitest single-run
npm run test:watch # watch mode
npm run test:coverage
```

Tests use `supertest` against an in-memory Express app; no real network
calls are made except in the GitHub-sync paths, which are isolated.

For full conformance against the upstream Python pytest plugin, boot the
included [`examples/server.ts`](./examples/server.ts) and point the
plugin at it:

```bash
pip install --pre bug-fab
npx tsx examples/server.ts &              # boots createBugFabRouter on :3000
pytest --bug-fab-conformance --base-url=http://localhost:3000/admin/bug-reports
```

## License

MIT. See [LICENSE](./LICENSE).

## See also

- [Bug-Fab][bf] — the parent project (Python reference adapter, frontend
  bundle, wire protocol).
- [`docs/PROTOCOL.md`][protocol] — wire-protocol spec.
- [`docs/protocol-schema.json`][schema] — authoritative JSON Schema.
- [`docs/CONFORMANCE.md`][conformance] — how to run the upstream pytest
  conformance plugin against this adapter.
- [`docs/ADAPTERS.md`][adapters] — sketches in other stacks.
- [`AGENTS.md`](./AGENTS.md) — guidance for AI assistants integrating
  against this package.

[schema]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json
[conformance]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/CONFORMANCE.md
[adapters]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS.md
