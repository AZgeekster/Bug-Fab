# Migration Notes — Hono / edge-runtime specifics

Things future consumers should know that don't fit in the README.

## One adapter, every runtime

`createBugFabApp({ storage })` returns a `Hono` instance, which is itself a Web `fetch`-style handler. The same instance runs unchanged on:

* **Cloudflare Workers** — `export default app` (or `export default { fetch: app.fetch }`).
* **Bun** — `Bun.serve({ fetch: app.fetch })`.
* **Deno** — `Deno.serve(app.fetch)`.
* **Vercel Edge Functions** — `export default app.fetch` from `api/[[...slug]].ts`.
* **Node.js** — `serve({ fetch: app.fetch, port: 3000 })` via `@hono/node-server`.

There is no per-runtime build step, no `dist/cloudflare/`, no `dist/node/`. The `examples/` tree is glue and storage selection, nothing more.

## Web-standard storage backends only

`MemoryStorage`, `R2Storage`, and `KVStorage` all build on Web-standard APIs — `Request`, `Response`, `File`, `ArrayBuffer`, `Uint8Array`, `crypto.subtle`. None of them import `node:fs`, `node:path`, `node:crypto`, or `Buffer`.

That deliberate constraint is what lets the same package boot on Cloudflare Workers and on Node 20 without conditional imports. If you write a custom `IStorage` and reach for `node:fs`, you've stepped off the supported runtime matrix — the package will load on Node but reject at deploy on Workers / Vercel Edge / Deno Deploy.

If you genuinely need filesystem persistence on a Node-only deployment, implement the backend in your own consumer code (it's a small interface) and inject it via `createBugFabApp({ storage: new MyFsStorage() })`. Don't add `node:fs` paths to this package.

## Trailing slashes on the viewer prefix

Hono v4 collapses both `/` and `''` sub-app paths to `${prefix}` (no trailing slash) when composed via `app.route()`. To keep `GET ${viewerPrefix}/` reachable (browsers love appending a trailing slash on directory-style URLs), the package wires the list handler on the parent app under the exact trailing-slash path. If you replace the default viewer or change the mount strategy, preserve that pairing — otherwise a direct visit to `/admin/bug-reports/` will 404.

## 4.5 MiB body cap on Vercel Edge

Vercel Edge Functions cap request bodies at 4.5 MiB. Bug-Fab's default screenshot ceiling is 10 MiB. Options:

* Keep screenshots small in the client (`html2canvas` at `scale: 0.6` typically lands under 4 MiB even for full-page captures).
* Move the collector to a Node serverless function (higher cap) or to Cloudflare Workers Paid (~100 MiB cap).
* Lower the package's `maxScreenshotBytes` so oversize uploads fail fast with the protocol's `413` envelope instead of a runtime crash.

## R2 vs KV vs Memory

* **MemoryStorage** — default; great for local dev and demos, lost on every redeploy. Don't ship to production.
* **R2Storage** — Cloudflare R2 + a `bug-fab/` key prefix. PNG bytes go straight to R2; JSON metadata is one object per report. Survives redeploys. The recommended production backend on Workers.
* **KVStorage** — Workers KV. Cheaper than R2 for very low write volume but capped at 25 MiB per value; intake will reject screenshots above ~24 MiB to leave headroom for JSON metadata. Reads are eventually consistent; if you list reports milliseconds after submitting one, the new report may be missing from the page.

Pick R2 for anything with real traffic. KV is the right call only when you're already on the Workers KV free tier and don't expect concurrent writes.

## CSP nonces

`createBugFabApp({ storage, cspNonce: (req) => req.headers.get('x-csp-nonce') })` opts the viewer HTML into nonce-based CSP. The provider runs once per page render, wrapped in try/catch so a misbehaving provider can't crash the page. Return `null` to skip nonce emission for that request.

## Auth

The package has no built-in auth. v0.1 expects the consumer to wrap the viewer routes in their own middleware (Cloudflare Access, a session check, a Bearer-token middleware, etc.). Apply the middleware to the parent app under the viewer prefix:

```typescript
const app = new Hono()
app.use('/admin/*', myAuthMiddleware)
mountBugFab(app, { storage })
```

The intake endpoint (`POST /api/bug-reports`) is intentionally open by default — consumers running on the public internet will typically front it with rate limiting at the platform layer (Cloudflare WAF, Vercel firewall) plus the package's built-in size and validation guards.

## Telemetry

Not yet emitted by this adapter. Hook points (intake received, status changed, deleted, bulk operations) are deferred to v0.2 to match the Python reference adapter's `bug_fab.events.*` taxonomy.
