# bug-fab-hono

Hono adapter for the [Bug-Fab](https://github.com/AZgeekster/Bug-Fab) wire protocol (v0.1).

Runs unchanged on every Hono target:

- Cloudflare Workers
- Bun (`Bun.serve`)
- Deno (`Deno.serve`)
- Vercel Edge Functions
- Node.js (with `@hono/node-server`)

The default storage backends (`MemoryStorage`, `R2Storage`, `KVStorage`) use **only Web-standard APIs** — no `node:fs`, no `Buffer`, no Node-only imports. That's the whole point: the package targets edge runtimes by default and runs everywhere else as a free side-effect.

## Status

First-party adapter, promoted 2026-05-21. 44/44 tests pass under `node:20` (intake + viewer + conformance). See [Bug-Fab Adapters Registry](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md) for the canonical status across all adapters.

## Install

```bash
npm install bug-fab-hono hono
# or
pnpm add bug-fab-hono hono
# or
bun add bug-fab-hono hono
```

`hono ^4` is a peer dependency.

## Quickstart — fresh Hono app

```typescript
import { Hono } from 'hono'
import { createBugFabApp, MemoryStorage } from 'bug-fab-hono'

const storage = new MemoryStorage()
const app = createBugFabApp({ storage })

// Bun:
Bun.serve({ port: 3000, fetch: app.fetch })

// Deno:
Deno.serve({ port: 3000 }, app.fetch)

// Cloudflare Workers (export default { fetch: app.fetch })
export default app
```

The defaults give you:

- `POST /api/bug-reports` — intake.
- `GET /admin/bug-reports/...` — viewer (HTML + JSON management).

## Quickstart — mount on an existing app

```typescript
import { Hono } from 'hono'
import { mountBugFab, MemoryStorage } from 'bug-fab-hono'

const app = new Hono()
app.get('/', (c) => c.text('My main app'))

mountBugFab(app, {
  storage: new MemoryStorage(),
  submitPrefix: '/api',
  viewerPrefix: '/admin/bugs',
})

export default app
```

## Cloudflare Workers — production storage

```typescript
import { createBugFabApp, R2Storage } from 'bug-fab-hono'

interface Env {
  BUG_FAB_R2: R2Bucket
  BUG_FAB_KV: KVNamespace
  GITHUB_PAT?: string
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const storage = new R2Storage({ bucket: env.BUG_FAB_R2, kv: env.BUG_FAB_KV })
    const app = createBugFabApp({
      storage,
      github: env.GITHUB_PAT
        ? { enabled: true, pat: env.GITHUB_PAT, repo: 'me/my-bugs' }
        : undefined,
    })
    return app.fetch(req)
  },
}
```

`wrangler.toml`:

```toml
name = "my-bug-collector"
main = "src/worker.ts"
compatibility_date = "2026-04-01"

[[r2_buckets]]
binding = "BUG_FAB_R2"
bucket_name = "bug-fab-screenshots"

[[kv_namespaces]]
binding = "BUG_FAB_KV"
id = "<your-kv-id>"
```

## Bun — `Bun.serve`

```typescript
import { createBugFabApp, MemoryStorage } from 'bug-fab-hono'

const app = createBugFabApp({ storage: new MemoryStorage() })

Bun.serve({ port: 3000, fetch: app.fetch })
console.log('Bug-Fab listening on http://localhost:3000')
```

For real persistence on Bun / Node, write your own `IStorage` against `bun:sqlite` or `node:fs` — see [`src/storage/README.md`](./src/storage/README.md) for the contributor pattern.

## Vercel Edge

`api/[...slug].ts`:

```typescript
import { createBugFabApp, MemoryStorage } from 'bug-fab-hono'

export const config = { runtime: 'edge' }

const app = createBugFabApp({ storage: new MemoryStorage() })

export default function handler(req: Request) {
  return app.fetch(req)
}
```

`vercel.json`:

```json
{
  "rewrites": [{ "source": "/api/(.*)", "destination": "/api/[...slug]" }]
}
```

Vercel Edge caps request body at 4.5 MiB — that's smaller than Bug-Fab's 10 MiB screenshot ceiling. Either keep screenshots small (`html2canvas` scale 0.6 typically lands well under 4 MiB) or upgrade to a regular Node serverless function with a larger cap.

## Body size limits across runtimes

| Runtime | Default body cap | Note |
|---|---|---|
| Cloudflare Workers (free) | ~6 MiB | Upgrade to Workers Paid for ~100 MiB. |
| Cloudflare Workers (paid) | ~100 MiB | Plenty for 10 MiB screenshots. |
| Bun.serve | unlimited | Adapter's 10 MiB check is authoritative. |
| Deno.serve | unlimited | Same. |
| Vercel Edge | 4.5 MiB | Below Bug-Fab default — see note above. |
| Node + @hono/node-server | unlimited | Same. |

The package enforces a 10 MiB screenshot cap inside the handler in every runtime. The runtime's own limit fires first if it's lower — verify deployment caps allow at least ~11 MiB total for the multipart envelope.

## Configuration

```typescript
interface BugFabAppOptions {
  storage: IStorage
  submitPrefix?: string                              // default "/api"
  viewerPrefix?: string                              // default "/admin/bug-reports", MUST be non-empty + non-root
  github?: { enabled, pat, repo, apiBase? }          // optional GitHub Issues sync
  rateLimit?: { enabled, maxRequests, windowMs }     // best-effort per-IP limiter
  viewerPermissions?: { can_edit_status, can_delete, can_bulk } // gate destructive endpoints
  cspNonce?: (req: Request) => string | null         // CSP nonce provider for viewer HTML
}
```

## CSP

The viewer renders inline `<script src="...bug-fab.js">` tags. Under a strict CSP (no `unsafe-inline`, no `strict-dynamic`), the browser will refuse them unless your CSP carries a nonce that matches the `nonce` attribute on the tag.

Wire it up via `cspNonce`:

```typescript
import { createBugFabApp } from 'bug-fab-hono'
import { Hono } from 'hono'

const app = new Hono()

// 1. Generate a nonce per request and stash it on `c`.
app.use('*', async (c, next) => {
  const buf = new Uint8Array(16)
  crypto.getRandomValues(buf)
  const nonce = btoa(String.fromCharCode(...buf))
  c.set('cspNonce', nonce)
  c.header('Content-Security-Policy', `script-src 'self' 'nonce-${nonce}'`)
  await next()
})

// 2. Wire bug-fab to read it back.
app.route(
  '/',
  createBugFabApp({
    storage: new MemoryStorage(),
    cspNonce: (req) => {
      // Hono doesn't expose c.get from outside middleware, so the
      // nonce ride-along has to come from a header or context map
      // your middleware sets on the Request itself. See repo/docs/CSP.md
      // for the full pattern.
      return req.headers.get('x-csp-nonce')
    },
  }),
)
```

If `cspNonce` is unset, the script tags ship without a nonce attribute, which is the correct fail-loud behavior under strict CSP.

## Conformance

The adapter is designed to pass the official Python conformance suite:

```bash
pip install --pre bug-fab
pytest --bug-fab-conformance --base-url=http://localhost:3000
```

Local conformance smoke tests live in `tests/conformance.test.ts` and run on `npm test`.

## Architecture notes

- **Multipart parsing** uses `c.req.parseBody()`, which delegates to the runtime's `Request.formData()` (Web Fetch standard). No `multer`-equivalent dependency.
- **Screenshot bytes** flow as `Uint8Array` end-to-end. `Buffer` would break Cloudflare Workers / Vercel Edge / Deno Deploy.
- **HTML rendering** uses Hono's built-in JSX runtime. No template engine, no Jinja-equivalent.
- **GitHub sync** uses `fetch`. Works in every runtime that ships fetch (every modern one).
- **`onError`** is installed at mount time so unhandled exceptions still emit the protocol's `{error, detail}` JSON envelope rather than Hono's default plain-text 500 — failing this is a common conformance trap.

## License

MIT — see [LICENSE](./LICENSE).
