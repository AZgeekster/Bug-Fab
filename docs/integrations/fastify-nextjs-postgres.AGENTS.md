# AI-Coder Companion: Bug-Fab × Fastify+Next.js+Postgres+PM2

This document is for AI coding assistants (Claude Code, Cursor, GitHub Copilot, etc.) performing the integration documented in [`fastify-nextjs-postgres.md`](./fastify-nextjs-postgres.md). Read the human guide first; this companion is the file-by-file prescriptive checklist with surface-the-decision callouts.

The human guide is **explanatory** (here's what's happening and why). This companion is **prescriptive** (here are the exact files to create, here are the exact edits to existing files, here are the questions you must ask the human before proceeding).

---

## 0. Pre-flight inspection

Before writing any code, inspect the consumer's repo and confirm assumptions:

```bash
# Confirm Fastify version (must be ≥ 5).
cat apps/api/package.json | grep '"fastify"'

# Confirm @fastify/multipart is registered.
grep -rn '@fastify/multipart' apps/api/src/

# Confirm there's a Drizzle setup.
ls apps/api/src/db/

# Confirm Next.js root layout location and whether it's a server component.
head -5 apps/web/src/app/layout.tsx

# Confirm the storage volume convention.
grep -rn 'STORAGE_DIR' apps/api/src/

# Confirm the existing PM2 ecosystem file.
cat ecosystem.config.js 2>/dev/null || cat ecosystem.config.cjs 2>/dev/null
```

If any of those probes turn up unexpected results (different file paths, missing tooling, etc.), **stop and ask the human** before proceeding. The integration assumes the standard layout above.

---

## 1. Decision points to surface to the human

Ask each of these explicitly **before** writing any code. These are real decisions, not guesses.

### 1.1 Database location

> "Bug-Fab needs two new tables (`bug_reports`, `bug_report_lifecycle`). Should they live in:
> (a) Your existing app's main schema (alongside other domain tables), or
> (b) A separate `bug_fab` Postgres schema (`CREATE SCHEMA bug_fab;`) for namespace isolation?"

(a) is simpler and matches the human guide. (b) is cleaner if your team draws hard schema boundaries.

### 1.2 Mount prefixes

> "The intake will mount at `/api/bug-reports` and the viewer at `/admin/bug-reports/...`. Are those URL prefixes free in your app, or should I pick different ones?"

Conflicts with existing routes will silently route to the wrong handler. Confirm before mounting.

### 1.3 Auth model

> "The intake (`POST /api/bug-reports`) will be **unauthenticated by default** — anyone visiting the app can file a bug report. The viewer (`/admin/bug-reports/*`) will require auth via an `onRequest` hook checking `req.session.userId`. Is that the right split, or do you want intake gated too?"

If your team requires auth on intake, expand the hook. If they want no auth on viewer (internal-only network deployments), drop the hook.

### 1.4 GitHub Issues sync

> "Should every submitted bug auto-create a GitHub Issue? If yes, which repo? You'll need a Personal Access Token with `repo` scope (private) or `public_repo` (public) — store it as a secret, not in code."

Default off. Turn on only if the human confirms a target repo.

### 1.5 Rate limiting

> "Bug-Fab can apply per-IP rate limiting on intake (default 10/hour). Want it on? If your intake is behind nginx / Cloudflare with rate limiting already, the in-app limiter is redundant."

Default off if external rate limiting is present; on otherwise.

### 1.6 Lifecycle `by` field

> "When an authenticated user changes a report's status via the viewer, do you want the lifecycle entry to record `req.session.userId` as the `by` value, or use the sentinel `'anonymous'`?"

Recording the userId is more useful but couples Bug-Fab to your session shape. The sentinel keeps Bug-Fab generic. Default: record the userId.

### 1.7 Frontend bundle source

> "Should I:
> (a) Copy `bug-fab.js` from the upstream Bug-Fab repo into `apps/web/public/bug-fab/` (current state), or
> (b) Pin to a specific Bug-Fab tag (e.g., `v0.1.0`) for reproducibility, or
> (c) Build the bundle from a local Bug-Fab clone (only if you're co-developing with Bug-Fab)?"

Default: (b) for production deployments; (a) for development.

---

## 2. Files to create

Each entry below is **literal** — file path, contents source, no improvisation.

### 2.1 `apps/api/src/db/schema/bug-reports.ts` (new)

Contents: copy the Drizzle schema block from [`fastify-nextjs-postgres.md` § Postgres schema](./fastify-nextjs-postgres.md#postgres-schema).

After creating: run `npm run db:generate && npm run db:migrate` from `apps/api/`.

**Validation:** `psql $DATABASE_URL -c '\dt bug_*'` should show both tables.

### 2.2 `apps/api/src/plugins/bug-fab/types.ts` (new)

Contents: copy [`repo/types/protocol.d.ts`](../../types/protocol.d.ts) verbatim from the Bug-Fab repo. Do **not** rewrite, paraphrase, or "improve" — Bug-Fab's protocol is the contract.

After creating: import from `'./types.js'` (or `'./types'` depending on the consumer's `tsconfig.json` `module` setting) in the other plugin files.

### 2.3 `apps/api/src/plugins/bug-fab/validation.ts` (new)

Contents: copy from [`docs/ADAPTERS.md` § Fastify](../ADAPTERS.md#fastify-typescript-fastify--5) — the `isValidImageBuffer`, `isValidSeverity`, `isValidStatus`, `isValidProtocolVersion`, and `validateSubmission` functions. **PNG only — do NOT add JPEG support** (the protocol is PNG-only as of v0.1).

**Validation:** unit-test the magic-byte check by feeding it (a) a real PNG buffer and (b) a JPEG buffer. PNG must return `true`, JPEG must return `false`.

### 2.4 `apps/api/src/plugins/bug-fab/errors.ts` (new)

Contents: factory functions returning `{ error: '<code>', detail: '<string>' }` for each documented error code in [`docs/PROTOCOL.md` § Error responses](../PROTOCOL.md). The relevant codes:

- `validation_error` (400)
- `schema_error` (422)
- `unsupported_protocol_version` (400)
- `payload_too_large` (413)
- `unsupported_media_type` (415)
- `not_found` (404)
- `rate_limited` (429)
- `internal_error` (500)
- `storage_unavailable` (503)

Do NOT add error codes the protocol doesn't define.

### 2.5 `apps/api/src/plugins/bug-fab/storage.ts` (new)

Contents: copy the `DrizzleStorage` class from [`fastify-nextjs-postgres.md` § Storage adapter](./fastify-nextjs-postgres.md#storage-adapter-drizzle).

**Adjustments before pasting:**
- Update the `import` for `bugReports`, `bugReportLifecycle` to your project's actual schema path.
- If your Drizzle setup uses `drizzle-orm/node-postgres` instead of `drizzle-orm/postgres-js`, update the type annotation accordingly.

**Validation:** import the class and instantiate it with a real DB connection — TypeScript should compile without errors.

### 2.6 `apps/api/src/plugins/bug-fab/routes/submit.ts` (new)

Contents: a Fastify route handler implementing `POST /bug-reports`. Use the intake-route code from [`docs/ADAPTERS.md` § Fastify § Intake route](../ADAPTERS.md#fastify-typescript-fastify--5) as the template. Adjustments:

- Pull `IStorage` from `../storage.js` (or the path your project uses).
- Pull `Errors` from `../errors.js`.
- Pull `validateSubmission`, `isValidImageBuffer` from `../validation.js`.
- The GitHub sync block reads `opts.github` from the plugin options — pass it through from the plugin entry.

### 2.7 `apps/api/src/plugins/bug-fab/routes/viewer.ts` (new)

Contents: 7 Fastify route handlers for the viewer endpoints:

- `GET ''` — root HTML list (renders a minimal HTML table of reports + filter chips)
- `GET '/reports'` — JSON list with filters
- `GET '/reports/:id'` — single report detail JSON
- `GET '/reports/:id/screenshot'` — serve the PNG file
- `PUT '/reports/:id/status'` — append lifecycle entry
- `DELETE '/reports/:id'` — hard delete
- `POST '/bulk-close-fixed'` — bulk status transition
- `POST '/bulk-archive-closed'` — bulk archive

Use [`docs/PROTOCOL.md`](../PROTOCOL.md) for the request/response shapes. The `:id` regex on every viewer route should match `/^bug-[A-Za-z]?\d{3,}$/` — invalid IDs return `404 not_found`, not `400`.

### 2.8 `apps/api/src/plugins/bug-fab/github-sync.ts` (new, optional)

Only create if the human said yes to GitHub sync (decision 1.4). Contents: copy from [TKR's plugin source](https://github.com/AZgeekster/Bug-Fab/tree/main/notes/tkr_corrected_plugin_2026-04-29/bug-fab/src/github-sync.ts) — it's the canonical reference for the best-effort sync pattern.

### 2.9 `apps/api/src/plugins/bug-fab/plugin.ts` (new)

Contents: the `bugFab` plugin from [`docs/ADAPTERS.md` § Fastify § Plugin shape](../ADAPTERS.md#fastify-typescript-fastify--5). Wraps with `fastify-plugin` (`fp()`) — this is mandatory; without it, parent-scope auth hooks won't fire for plugin routes.

### 2.10 `apps/api/src/plugins/bug-fab/index.ts` (new)

Contents:
```typescript
export { bugFab } from './plugin.js'
export { DrizzleStorage } from './storage.js'
export type * from './types.js'
```

---

## 3. Files to edit (existing)

### 3.1 `apps/api/src/index.ts`

**Add** (after the existing `@fastify/multipart` registration, before any catch-all routes):

```typescript
import { bugFab, DrizzleStorage } from './plugins/bug-fab/index.js'
import { db } from './db/client.js'

const bugStorage = new DrizzleStorage({
  db,
  screenshotDir: `${process.env.STORAGE_DIR ?? './storage'}/bug-reports`,
})

// Auth hook for viewer routes — must come BEFORE app.register(bugFab, ...).
app.addHook('onRequest', async (req, reply) => {
  if (req.url.startsWith('/admin/bug-reports')) {
    if (!req.session?.userId) {
      return reply.status(401).send({
        error:  'unauthorized',
        detail: 'Sign in to view bug reports.',
      })
    }
  }
})

await app.register(bugFab, {
  storage:      bugStorage,
  submitPrefix: '/api',
  viewerPrefix: '/admin/bug-reports',
  github: process.env.BUG_FAB_GITHUB_ENABLED === 'true' ? {
    enabled: true,
    pat:     process.env.BUG_FAB_GITHUB_PAT!,
    repo:    process.env.BUG_FAB_GITHUB_REPO!,
  } : undefined,
})
```

**Do NOT** modify the existing multipart registration. **Do NOT** wrap the bugFab routes in your app's response envelope (`ok()`/`fail()`). The protocol uses bare JSON.

### 3.2 `apps/web/src/app/layout.tsx`

If the file is a server component (no `'use client'` at the top — confirm in pre-flight 0):

**Add** inside the `<body>`, after `{children}`:

```tsx
import Script from 'next/script'

// ... inside <body>:
<Script src="/bug-fab/bug-fab.js" strategy="afterInteractive" />
<Script id="bug-fab-init" strategy="afterInteractive">{`
  window.addEventListener("DOMContentLoaded", () => {
    if (!window.BugFab) return;
    window.BugFab.init({
      submitUrl:      '${process.env.NEXT_PUBLIC_API_BASE ?? ""}/api/bug-reports',
      html2canvasUrl: '/bug-fab/vendor/html2canvas.min.js',
      appVersion:     '${process.env.NEXT_PUBLIC_APP_VERSION ?? "dev"}',
      environment:    '${process.env.NODE_ENV}',
    });
  });
`}</Script>
```

If the file IS a client component, use the `useEffect` pattern from [`fastify-nextjs-postgres.md` § Frontend setup §3](./fastify-nextjs-postgres.md#3-optional-client-component-init) instead.

### 3.3 `ecosystem.config.js`

**Add** to the `env` block of the `api` app:

```javascript
STORAGE_DIR:                  '/var/lib/your-app/storage',
BUG_FAB_GITHUB_ENABLED:       'false',  // flip to 'true' once configured
BUG_FAB_RATE_LIMIT_ENABLED:   'true',
BUG_FAB_RATE_LIMIT_MAX:       '10',
BUG_FAB_RATE_LIMIT_WINDOW_MS: '3600000',
```

**Do NOT** put `BUG_FAB_GITHUB_PAT` in this file — that's a secret and goes in your secret manager / `.env` (gitignored).

### 3.4 `.env` (gitignored, dev) and `.env.production.example` (committed, template)

Add the variables documented in [`fastify-nextjs-postgres.md` § Configuration](./fastify-nextjs-postgres.md#configuration--environment-variables).

---

## 4. Static-file injection

After 2.x and 3.x are done:

```bash
# From repo root:
mkdir -p apps/web/public/bug-fab/vendor

# Pin to a Bug-Fab tag (recommended for production).
TAG=v0.1.0a1   # or whatever the latest stable tag is
curl -L "https://raw.githubusercontent.com/AZgeekster/Bug-Fab/${TAG}/static/bug-fab.js" \
  -o apps/web/public/bug-fab/bug-fab.js
curl -L "https://raw.githubusercontent.com/AZgeekster/Bug-Fab/${TAG}/static/vendor/html2canvas.min.js" \
  -o apps/web/public/bug-fab/vendor/html2canvas.min.js
```

Document the upstream tag in your repo's `apps/web/public/bug-fab/README.md` (one line: "Bug-Fab static bundle, pinned to v0.1.0a1.") so future-you knows what version is deployed.

---

## 5. Verification checklist

After all of section 2-4 is done, run through this. **Do not declare the integration complete until every item passes.**

1. **TypeScript compiles** — `npm run build --workspace=apps/api` exits 0. No type errors.
2. **API boots** — `npm run dev:api` starts without errors. Watch for `[bug-fab] Intake: POST /api/bug-reports | Viewer: /admin/bug-reports/reports` in the log.
3. **DB migrations applied** — `psql $DATABASE_URL -c '\dt bug_*'` shows both tables.
4. **Frontend bundle loads** — open the Next.js app in a browser, check the network panel; `/bug-fab/bug-fab.js` should return 200, `/bug-fab/vendor/html2canvas.min.js` should return 200.
5. **Floating button appears** — bottom-right corner of the page should show the bug-fab icon. Click expands the modal.
6. **Submit a real bug report** — fill in the modal, click Submit. Expect:
   - Network panel: `POST /api/bug-reports` returns `201`.
   - Response body has `id`, `received_at`, `stored_at`, `github_issue_url`.
7. **Viewer shows the report** — navigate to `/admin/bug-reports` (assuming you're logged in). The report you just submitted should be in the list.
8. **Detail page renders** — click the report. Title, description, screenshot, lifecycle log should render.
9. **Status update works** — change status from `open` to `investigating`. Reload; status persists. A new lifecycle entry should appear.
10. **Conformance suite passes** — `pip install --pre bug-fab && pytest --bug-fab-conformance --base-url=http://localhost:3000`. Either all pass, or any failures are documented as known-skipped reasons.
11. **PM2 boot is clean** — `pm2 start ecosystem.config.js && pm2 logs api`. No error spam in the logs.
12. **Persistent volume survives restart** — `pm2 restart api`, submit another report, verify storage and DB still work.

---

## 6. What to do if conformance fails

| Failure category | Likely cause | Fix |
|------------------|-------------|-----|
| `severity` accepts unknown values | Storage layer is silently coercing | Make sure `validation.ts` rejects with `422` BEFORE the storage call |
| `protocol_version` not validated | Missing required-field check | Add `validateSubmission` early in the route handler |
| Multipart 413 returns wrong shape | `@fastify/multipart` size limit not set, or per-route check | Set `limits.fileSize` on plugin registration, NOT in the route |
| Status update changes wrong field | DB schema mismatch with PROTOCOL.md | Check `bug_reports.status` enum matches `open\|investigating\|fixed\|closed` exactly |
| Lifecycle log is empty | Forgot to insert the `created` lifecycle row in `saveReport` | Re-read `DrizzleStorage.saveReport` — it uses a transaction with both inserts |
| Viewer routes return your app's `{ data, error }` envelope | Global error handler intercepting | Move Bug-Fab routes outside the global handler, or add an exemption for `/admin/bug-reports/*` and `/api/bug-reports` |

---

## 7. Anti-patterns

These are the things AI assistants commonly do wrong with this protocol. Don't.

- **Silent enum coercion.** "If `severity` is invalid, default it to `medium`." NO. Reject with `422 schema_error`. The conformance suite has an explicit test that submits `severity: "urgent"` and expects rejection.
- **Trusting client User-Agent.** `metadata.context.user_agent` is what the client claims. Capture the real one from `request.headers['user-agent']` and store it in `server_user_agent`. Both round-trip; only the server-captured one is authoritative.
- **JPEG screenshot support.** v0.1 is PNG-only. The 415 test specifically submits a JPEG and expects rejection.
- **Rewriting field shapes.** `context.url` lives at `metadata.context.url`, not `metadata.url`. The flattened-context shape is the spec — the JSON Schema at `repo/docs/protocol-schema.json` is the contract.
- **Adding required fields the spec doesn't have.** If your team wants `bug_reporter.team_id` as required, store it in `context.team_id` (allowed via the `extra="allow"` rule) — don't add it to the top-level schema.
- **Per-route multipart-size checks.** `@fastify/multipart` enforces at the parser level. Per-route checks are redundant and run after the buffer is already in memory.
- **Wrapping responses in your app's envelope.** Bug-Fab routes return bare JSON for success and `{ error, detail }` for failure. If your app uses an `ok()`/`fail()` helper, exclude Bug-Fab routes.
- **Mounting the viewer at `/`.** The plugin throws at startup. Pick a non-empty prefix.
- **Forgetting `fastify-plugin` (`fp()`)**. The plugin must be wrapped or auth hooks won't fire for plugin routes.

---

## 8. Out-of-scope guardrails

The integration is the *application of Bug-Fab to your project*, not a Bug-Fab redesign. You should NOT:

- Modify the upstream Bug-Fab repo. (Bug-Fab is a separate codebase; bug fixes go via PR upstream.)
- Re-implement protocol parts your way. (The conformance suite catches drift.)
- Skip `protocol_version` to "make it simpler." (Required field; pre-flight rejects).
- Promote `report_type` from optional to required, or vice versa. (Spec.)
- Add custom statuses (`pending_qa`, `in_review`). (v0.1 enum is locked.)
- Edit the static bundle (`bug-fab.js`). (Pin to an upstream tag; if you need a new feature, request it upstream.)

If you find yourself wanting to do any of those, **stop and surface the request to the human** — likely Bug-Fab itself needs a change, or your requirement isn't a Bug-Fab fit.

---

## 9. After the integration is live

1. **Run the conformance suite once a quarter** to catch regressions from upstream Bug-Fab updates.
2. **Watch upstream Bug-Fab releases** — subscribe to GitHub releases at `https://github.com/AZgeekster/Bug-Fab/releases`. v0.2 will introduce an auth abstraction; you'll want to migrate when it ships.
3. **Update the bundle pin** when upstream patches the static frontend (e.g., html2canvas security fixes).
4. **File issues upstream** if you hit a real bug or spec ambiguity. Bug-Fab is a small project; consumer feedback shapes the roadmap directly.

---

## 10. Quick reference

| Task | Command / file |
|------|----------------|
| Re-generate types from upstream | Copy `repo/types/protocol.d.ts` from the pinned tag |
| Run the conformance suite | `pip install --pre bug-fab && pytest --bug-fab-conformance --base-url=http://localhost:3000` |
| Boot the API locally | `npm run dev:api` |
| Submit a test report via curl | See `notes/tkr_corrected_plugin_2026-04-29/bug-fab/AGENTS.md` § "Quick Smoke Test" in the Bug-Fab maintainer's workspace, or [`docs/AGENTS.md`](../../AGENTS.md) |
| Inspect a stored report | `psql $DATABASE_URL -c "SELECT * FROM bug_reports WHERE id = 'bug-001';"` |
| Re-deploy under PM2 | `pm2 reload api` |

---

This companion is **prescriptive on purpose.** The human guide gives reasons; this gives instructions. If reasons help your workflow, read both. If you just want to get the integration done in one focused session, work through sections 0 → 9 in order.
