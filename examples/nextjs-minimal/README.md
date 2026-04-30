# Bug-Fab — Next.js minimal example

A complete Next.js 14 App Router POC implementing **all eight Bug-Fab
wire-protocol endpoints** as Route Handlers. Proves that Next.js can be
its own Bug-Fab adapter — no separate backend process required.

This sits alongside the [`fastapi-minimal/`](../fastapi-minimal/),
[`flask-minimal/`](../flask-minimal/), and [`react-spa/`](../react-spa/)
examples. They all speak the same wire protocol; the difference is which
stack hosts the eight endpoints.

## Versions

| Tool        | Version |
| ----------- | ------- |
| Next.js     | 14      |
| Node        | ≥ 20    |
| TypeScript  | 5       |

`tsconfig.json` is strict (`"strict": true`). No external runtime
dependencies beyond Next.js, React, and React DOM.

## What's wired up

| Method   | Path                                                | File                                                                |
| -------- | --------------------------------------------------- | ------------------------------------------------------------------- |
| `POST`   | `/api/bug-reports`                                  | [`src/app/api/bug-reports/route.ts`](src/app/api/bug-reports/route.ts) |
| `GET`    | `/admin/bug-reports/reports`                        | [`src/app/admin/bug-reports/reports/route.ts`](src/app/admin/bug-reports/reports/route.ts) |
| `GET`    | `/admin/bug-reports/reports/{id}`                   | [`src/app/admin/bug-reports/reports/[id]/route.ts`](src/app/admin/bug-reports/reports/[id]/route.ts) |
| `DELETE` | `/admin/bug-reports/reports/{id}`                   | (same file as above — Route Handlers route by HTTP method) |
| `PUT`    | `/admin/bug-reports/reports/{id}/status`            | [`src/app/admin/bug-reports/reports/[id]/status/route.ts`](src/app/admin/bug-reports/reports/[id]/status/route.ts) |
| `GET`    | `/admin/bug-reports/reports/{id}/screenshot`        | [`src/app/admin/bug-reports/reports/[id]/screenshot/route.ts`](src/app/admin/bug-reports/reports/[id]/screenshot/route.ts) |
| `POST`   | `/admin/bug-reports/bulk-close-fixed`               | [`src/app/admin/bug-reports/bulk-close-fixed/route.ts`](src/app/admin/bug-reports/bulk-close-fixed/route.ts) |
| `POST`   | `/admin/bug-reports/bulk-archive-closed`            | [`src/app/admin/bug-reports/bulk-archive-closed/route.ts`](src/app/admin/bug-reports/bulk-archive-closed/route.ts) |

A simple HTML index lives at `/admin/bug-reports`
([`src/app/admin/bug-reports/page.tsx`](src/app/admin/bug-reports/page.tsx)).

The wire-protocol library is in [`src/lib/bug-fab/`](src/lib/bug-fab/):
TypeScript types (verbatim from `repo/types/protocol.d.ts`), validation,
error envelopes, and the disk-backed `FileStorage` class.

## Install

```bash
cd repo/examples/nextjs-minimal
npm install
```

## Frontend bundle setup

The Next.js layout loads `bug-fab.js` from `public/bug-fab/` at runtime
but the bundle is **not committed** — see
[`public/bug-fab/README.md`](public/bug-fab/README.md). Before the first
run, copy the bundle in:

```bash
# From this directory:
mkdir -p public/bug-fab/vendor
cp ../../static/bug-fab.js public/bug-fab/
cp ../../static/vendor/html2canvas.min.js public/bug-fab/vendor/
```

If you skip this, the page still loads but the floating bug icon never
appears (`window.BugFab` is undefined and the init script is a no-op).

## Run

```bash
npm run dev
# → open http://localhost:3000
```

Click the small bug icon in the bottom-right corner. Annotate the
screenshot if you like, fill in a title, click submit. Submitted reports
land in `./bug_reports/` next to `package.json`:

```
bug_reports/
├── index.json
├── bug-001.json
└── bug-001.png
```

## View reports

- HTML index: <http://localhost:3000/admin/bug-reports>
- JSON list: <http://localhost:3000/admin/bug-reports/reports>
- One report: `GET /admin/bug-reports/reports/bug-001`
- Screenshot: `GET /admin/bug-reports/reports/bug-001/screenshot`

If `ADMIN_TOKEN` is set in `.env.local`, the JSON / mutation endpoints
require an `x-admin-token: <value>` request header. With no token set,
the POC auto-allows for convenience — see
[Production caveats](#production-caveats).

```bash
# Example admin call with a token
curl -H "x-admin-token: dev-token-change-me" \
  http://localhost:3000/admin/bug-reports/reports
```

## Conformance verification

The Python conformance suite at
[`repo/bug_fab/conformance/`](../../bug_fab/conformance/) runs against
**any** HTTP server that speaks the v0.1 wire protocol. With this POC
booted on `localhost:3000`:

```bash
# In a separate terminal — Python side, from the repo root:
pip install -e ".[dev]"
pytest \
  --bug-fab-conformance \
  --base-url=http://localhost:3000/api \
  --viewer-base-url=http://localhost:3000/admin/bug-reports
```

`--base-url` points at the intake mount; `--viewer-base-url` points at
the viewer mount. The suite exercises:

- Multipart parsing + the JPEG-rejection magic-byte test.
- The unknown-protocol-version 400 path.
- The locked-enum 422 path (`severity: "urgent"` is the canonical bait).
- Listing, filtering, status updates, deletes, and the two bulk endpoints.

If you've set `ADMIN_TOKEN`, you'll need to extend the conformance
runner with an `x-admin-token` header (or unset the env var for the
duration of the test run).

## Architecture notes

### `runtime = 'nodejs'`

Every Route Handler that touches `node:fs` (intake, screenshot serve,
all viewer routes that talk to `FileStorage`) declares `export const
runtime = 'nodejs'`. The default Edge runtime cannot read from disk;
without this annotation the routes silently 500 in production.

### Body size cap

[`next.config.js`](next.config.js) sets
`experimental.serverActions.bodySizeLimit: '11mb'`. PROTOCOL.md caps the
total request at 11 MiB (10 MiB screenshot + ~1 MiB metadata + multipart
overhead); the Next.js default of 1 MB silently 413s a normal high-DPI
capture before the Route Handler ever runs.

### Storage location and ID format

`FileStorage` writes to `./bug_reports/` next to `package.json`
(override with `BUG_FAB_STORAGE_DIR`). IDs match the protocol regex
`^bug-[A-Za-z]?\d{3,}$` — the default emits `bug-001`, `bug-002`, etc.;
set `BUG_FAB_ID_PREFIX=P` to get `bug-P001` for multi-environment
deployments that share a collector.

Atomic writes use tmp + `rename`. Concurrency is serialized through an
in-process promise chain — single-process only.

### What's deliberately omitted

- **Database storage.** The POC ships disk-only by design.
- **GitHub Issues sync.** Stubbed with a comment block in
  [`src/lib/bug-fab/storage.ts`](src/lib/bug-fab/storage.ts) that points
  at the protocol contract for the pattern.
- **Real auth.** `checkAdminToken` is a placeholder env-var check.

## Production caveats

- **Serverless platforms (Vercel, Cloudflare Pages) have no writable
  filesystem.** `FileStorage` will fail. Replace with an `IStorage`
  that targets S3 / R2 / KV; the rest of the POC stays the same.
- **`FileStorage` is single-process.** The in-memory mutex chain that
  serializes index writes does not span workers. Multiple `next start`
  workers (or any horizontal scale) racing on the same `index.json`
  will lose updates. Use a real DB at scale.
- **Auth is illustrative.** Replace `checkAdminToken` with NextAuth.js,
  Clerk, or a `middleware.ts` path matcher tied to your existing auth
  infrastructure.
- **Rate limiting is not implemented.** The protocol allows `429
  rate_limited` responses; per-IP limiting belongs in production
  middleware (and the protocol is intentionally explicit that
  unprotected intake is acceptable for hobby projects, not for
  internet-facing services).
- **GitHub sync is stubbed.** See
  [`src/lib/bug-fab/storage.ts`](src/lib/bug-fab/storage.ts) for the
  shape; failure mode MUST be best-effort (log + return
  `github_issue_url: null`), never fail the intake.

## Where the protocol lives

- [`repo/docs/PROTOCOL.md`](../../docs/PROTOCOL.md) — wire-protocol
  spec.
- [`repo/docs/protocol-schema.json`](../../docs/protocol-schema.json) —
  authoritative JSON Schema.
- [`repo/types/protocol.d.ts`](../../types/protocol.d.ts) — TypeScript
  types (this POC's
  [`src/lib/bug-fab/types.ts`](src/lib/bug-fab/types.ts) is a verbatim
  copy).
- [`repo/docs/ADAPTERS.md`](../../docs/ADAPTERS.md) — reference
  sketches for non-Python adapters; the "Next.js Route Handlers"
  section is the design this POC realizes.
