# AGENTS.md — guidance for AI assistants integrating with `bug-fab-express`

This file is targeted at AI coding assistants (Claude Code, Copilot, Cursor,
Aider, etc.) helping a developer wire `bug-fab-express` into a real Express
app. It captures the conventions, traps, and "do not refactor" zones that
trip generic codegen.

If you are a human, [README.md](./README.md) is what you want.

## TL;DR for assistants

- This package implements a [public wire protocol][protocol]. Field names
  are **snake_case** across the wire. Do **not** add a camelCase converter,
  do **not** rewrite request/response shapes — both will silently break
  conformance with the Python reference adapter.
- The viewer router serves an **HTML list at the mount-point root**. Do
  not suggest mounting at `/` — that collapses the viewer with the app's
  home page.
- The HTML viewer's links and screenshot URLs are built from
  **`req.baseUrl`** at request time, captured inside `viewer.ts`. Do not
  reintroduce a construction-time `mountPath` parameter on
  `createBugFabRouter` or `registerViewerRoutes` — an earlier iteration
  hardcoded it to `''` and produced broken root-absolute hrefs under any
  non-root mount (audit 2026-05-01_express_adapter Drift C).
- All non-2xx responses follow a strict envelope: `{ error, detail }`. Do
  not invent ad-hoc error shapes; use `Errors.*` from `./src/errors.ts`.
- The PNG magic-byte check is **load-bearing**. Some browsers mis-label
  PNGs and html2canvas's behavior varies by version. Trust the bytes,
  not the Content-Type.
- GitHub sync is **best-effort by contract**. Never raise a non-2xx from
  intake or status update because GitHub failed. The adapter handles this;
  do not refactor it into a "fail loudly" path.

[protocol]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md

## File map

| Path                              | What lives here                                       |
|-----------------------------------|-------------------------------------------------------|
| `src/index.ts`                    | Public API exports. Edit only to expose new symbols.  |
| `src/router.ts`                   | `createBugFabRouter(opts)` factory. Wires intake + viewer. |
| `src/intake.ts`                   | `POST /bug-reports` handler + multer config.          |
| `src/viewer.ts`                   | The other seven endpoints + HTML viewer routes.       |
| `src/types.ts`                    | Wire-protocol TypeScript types. Mirrors `protocol-schema.json`. |
| `src/validation.ts`               | Magic-byte + schema validation. Strict-reject by design. |
| `src/errors.ts`                   | `{ error, detail }` envelope factories.               |
| `src/github.ts`                   | Best-effort GitHub Issues sync.                       |
| `src/storage/IStorage.ts`         | The 9-method storage contract (re-export of types).   |
| `src/storage/FileStorage.ts`      | Default file-on-disk backend.                         |
| `src/templates/index.ts`          | Tiny HTML render helpers for the viewer.              |
| `tests/*.test.ts`                 | Vitest + supertest integration tests.                 |
| `tests/conformance.test.ts`       | Smoke check across all 8 endpoints + envelope shape.  |

## Workflows you will be asked to do

### "Wire bug-fab-express into my Express app"

1. Add `bug-fab-express`, `express`, and `multer` to `package.json`.
2. Pick a mount path. Default to `/admin/bug-reports` unless the user has
   an existing admin namespace. **Never** mount at `/`.
3. Choose a storage backend:
   - `FileStorage` for single-process apps and POCs.
   - A custom `IStorage` impl backed by the user's existing DB for
     production / clustered deployments.
4. Decide auth:
   - If the app already has admin middleware, mount the router after it.
   - If not, surface that to the user explicitly — Bug-Fab v0.1 has no
     auth abstraction, so the mount-point is the only enforcement seam.
5. Embed the [Bug-Fab frontend bundle][bundle] on whichever pages should
   show the FAB. The bundle is a separate concern; it talks to the
   adapter via the public wire protocol.

[bundle]: https://github.com/AZgeekster/Bug-Fab/tree/main/static

Reference snippet:

```ts
import express from 'express'
import { createBugFabRouter, FileStorage } from 'bug-fab-express'

const app = express()
const storage = new FileStorage({ storageDir: process.env.BUG_FAB_DIR ?? './var/bug_fab' })
app.use('/admin/bug-reports', createBugFabRouter({ storage }))
```

### "Add a custom storage backend"

Implement `IStorage` from `bug-fab-express`. The contract is 9 methods +
an optional `setGitHubIssue` post-save hook. Read `src/storage/FileStorage.ts`
end-to-end before starting — it's the only reference implementation here.

Critical methods:

- `saveReport(metadata, screenshotBytes) → id` — assigns an id matching
  the regex `^bug-[A-Za-z]?\d{3,}$`.
- `listReports(filters, page, pageSize)` — must include the `stats` block
  (`{ open, investigating, fixed, closed }`) computed across the
  pre-paginated, pre-status-filtered set. See `FileStorage.listReports`.
- `bulkCloseFixed()` / `bulkArchiveClosed()` — return only the count of
  reports actually transitioned (no-ops are not counted).

### "Migrate from a hand-rolled bug reporter"

Read [the upstream protocol doc][protocol] before suggesting field
mappings. Common shape mismatches that cost real time:

| Hand-rolled name    | Bug-Fab name             | Notes                          |
|---------------------|--------------------------|--------------------------------|
| `description`       | `description`            | Optional in v0.1 (was required everywhere else). |
| `client_timestamp`  | `client_ts`              | Required, opaque, ISO 8601.    |
| `userAgent`         | (split)                  | `context.user_agent` (client-reported) + top-level `server_user_agent` (request header, source of truth). |
| `priority`          | `severity`               | Locked enum: low/medium/high/critical. **No coercion.** |
| `state`             | `status`                 | Locked enum: open/investigating/fixed/closed. |
| `attachments[]`     | (none in v0.1)           | Multi-attachment is a v0.2 candidate. |
| `reporter` (string) | `reporter` (object)      | `{ name?, email?, user_id? }`, each ≤ 256 chars. |

## Things that look like bugs but are intentional

- **`createBugFabRouter` mounts `express.json()` only on the status PUT
  route.** Globally enabling `express.json()` on the parent app is fine —
  multer reads `req.file.buffer`, not `req.body`. The per-route mount
  protects intake when the parent app doesn't already have JSON parsing.
- **The viewer's HTML `GET /` returns the list, not a redirect to
  `/reports`.** This is deliberate — the protocol's mount-prefix invariant
  reserves the bare prefix for the human viewer.
- **The viewer's templates take `mountPath` as a per-call argument and
  the viewer routes pass `req.baseUrl` for it.** This is the load-bearing
  invariant. Do not refactor it back into a constant captured at
  `registerViewerRoutes` setup time. If you find yourself writing
  `registerViewerRoutes(..., '')` or `renderListPage({ mountPath: '' ... })`
  outside a unit test, stop and re-read `viewer.ts`'s mount-path comment.
- **`stored_at` is `file://...`-shaped for FileStorage but opaque
  otherwise.** The protocol does not validate `stored_at` format. Other
  backends MAY return any human-readable persistence reference. Do not
  parse this on the client side.
- **`updateStatus` with the same status returns 200 with no lifecycle
  append.** This is the documented idempotent no-op path. v0.1 lets
  adapters choose either "always append" or "no-op the audit"; this
  package picks no-op.

## Things you should NOT do

- **Do not add a JSON Schema validator with auto-coerce.** AJV with
  `coerceTypes: true` will silently rewrite `severity: "urgent"` into
  something else and the Bug-Fab conformance suite will fail.
- **Do not refactor `multer` away.** Express's built-in multipart support
  is limited; multer is the de facto choice in this corner of the
  ecosystem and the wire protocol assumes streaming-friendly multipart.
- **Do not introduce a database driver as a hard dependency.**
  `FileStorage` is the default; SQL backends ship as user-provided
  `IStorage` impls.
- **Do not reference private or internal project names** in source,
  docs, or tests. The upstream Bug-Fab project keeps a strict
  public/private boundary; this package is on the public side. Use
  generic example names (e.g. `octocat/octocat-internal-tools`) instead
  of any real consumer's identifier.

## Verification quick reference

```bash
npm run typecheck      # tsc --noEmit
npm test               # vitest single-run
npm run build          # compile to dist/
```

For full conformance against the upstream Python plugin:

```bash
pip install --pre bug-fab
npx tsx examples/server.ts
pytest --bug-fab-conformance --base-url=http://localhost:3000/admin/bug-reports
```

## When in doubt

- Wire format question? → [`protocol-schema.json`][schema] is authoritative.
- Behavior question? → [`PROTOCOL.md`][protocol] prose, then the FastAPI
  reference (`bug_fab/`).
- Express-specific question? → this README + the source comments.
- Anything else? → file an issue on the [Bug-Fab repo][bf] tagged
  `adapter:express`.

[schema]: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json
[bf]: https://github.com/AZgeekster/Bug-Fab
