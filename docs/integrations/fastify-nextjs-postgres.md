# Bug-Fab Integration — Fastify + Next.js + PostgreSQL + PM2

A complete walkthrough for adding Bug-Fab to a Fastify ≥ 5 backend with a Next.js ≥ 14 (App Router) frontend, PostgreSQL persistence via Drizzle ORM, and PM2 process management. Validated against [TKR](#references), Bug-Fab's first Node consumer.

**Reading order:** if you're integrating from scratch, follow the sections top-to-bottom. If you already have a Bug-Fab adapter implementation and want a quick reference for the deployment-side concerns (PM2, Postgres schema, Next.js Script-tag injection), jump to [Frontend setup](#frontend-setup), [PM2 deployment](#pm2-deployment), and [Postgres schema](#postgres-schema).

---

## Contents

1. [What you'll have when done](#what-youll-have-when-done)
2. [Prerequisites](#prerequisites)
3. [Architecture overview](#architecture-overview)
4. [Backend setup](#backend-setup)
5. [Postgres schema](#postgres-schema)
6. [Storage adapter (Drizzle)](#storage-adapter-drizzle)
7. [Plugin registration](#plugin-registration)
8. [Auth (onRequest hook)](#auth-onrequest-hook)
9. [Frontend setup](#frontend-setup)
10. [PM2 deployment](#pm2-deployment)
11. [Configuration & environment variables](#configuration--environment-variables)
12. [Conformance verification](#conformance-verification)
13. [Operations](#operations)
14. [Common gotchas](#common-gotchas)
15. [Upgrade path](#upgrade-path)
16. [References](#references)

---

## What you'll have when done

- A floating bug-report button in the bottom-right corner of every Next.js page (authenticated routes), produced by the official Bug-Fab static bundle.
- 8 backend endpoints under `/api/bug-reports` (intake) and `/admin/bug-reports/*` (viewer + management) handled by a Fastify plugin.
- Bug reports persisted in your existing PostgreSQL database via Drizzle ORM in two new tables (`bug_reports`, `bug_report_lifecycle`). Screenshots on disk in your existing storage volume.
- Optional GitHub Issues sync — every submitted bug auto-creates an issue in a target repo, with status syncing back when the report's status changes.
- Conformance verified against the Python `bug-fab-conformance` pytest plugin.
- Deployment under PM2 alongside your other Fastify processes.

You will **not** need to install any Bug-Fab npm package as of v0.1 — you'll write a thin Fastify plugin against the Bug-Fab wire protocol, using [`docs/protocol-schema.json`](../protocol-schema.json) as the type source. The reference for what the plugin should look like is [`docs/ADAPTERS.md` § Fastify](../ADAPTERS.md#fastify-typescript-fastify--5).

---

## Prerequisites

| Component | Required version | Notes |
|-----------|-----------------|-------|
| Fastify | ≥ 5.0 | Fastify 4 also works but the plugin shapes below assume v5's promise-only API. |
| `@fastify/multipart` | ≥ 10.0 | Already pulled in by most Fastify apps that handle file uploads. |
| `fastify-plugin` | ≥ 5.0 | Required so parent-scope auth hooks fire for Bug-Fab routes. |
| Node.js | ≥ 20 LTS | Native `fetch`, `Buffer.subarray`, and other primitives the plugin uses. |
| Next.js | ≥ 14 (App Router) | Pages Router works too; layout instructions differ. |
| PostgreSQL | ≥ 13 | `jsonb` and timezone-aware timestamps. |
| Drizzle ORM | ≥ 0.30 | Or any other ORM — the schema below is portable. |
| PM2 | ≥ 5.3 | Or any process manager (`systemd`, `forever`, `supervisord`). PM2 is the documented default. |
| Python | ≥ 3.10 | Only required to run the conformance suite; not at runtime. |

You should already have:

- A working Fastify app under `apps/api/` (or wherever your backend lives) with `@fastify/multipart` registered.
- A Next.js app under `apps/web/` (or wherever) with App Router.
- A Postgres database with Drizzle migrations set up.
- An existing PM2 ecosystem file you can extend.

If any of those is missing, set it up first — this guide doesn't cover greenfield Fastify / Next.js / Drizzle bootstrapping.

---

## Architecture overview

```
        ┌────────────────────────────────────────────────────────┐
        │                      User's browser                     │
        │                                                         │
        │  Next.js page                                           │
        │   └── <Script src="/bug-fab.js">                        │
        │        └── window.BugFab.init({                         │
        │              submitUrl: "/api/bug-reports", ...         │
        │            })                                           │
        │                                                         │
        │  Floating bug button → modal → POST multipart           │
        └─────────────────┬───────────────────────────────────────┘
                          │
                          │  POST /api/bug-reports (multipart)
                          │  GET  /api/bug-fab/static/* (bundle assets)
                          ▼
        ┌────────────────────────────────────────────────────────┐
        │                  Fastify backend (PM2)                  │
        │                                                         │
        │  apps/api/src/index.ts                                  │
        │   └── bugFab plugin                                     │
        │        ├── submit routes  (POST /api/bug-reports)       │
        │        ├── viewer routes  (/admin/bug-reports/*)        │
        │        ├── DrizzleStorage                               │
        │        │    └── pg + Drizzle ORM                        │
        │        └── github-sync   (best-effort)                  │
        └────────┬─────────────────────────────────┬──────────────┘
                 │                                  │
                 ▼                                  ▼
        ┌──────────────────┐              ┌──────────────────────┐
        │   PostgreSQL     │              │  $STORAGE_DIR/       │
        │                  │              │   bug-reports/       │
        │ bug_reports      │              │     bug-001.png      │
        │ bug_report_      │              │     bug-002.png      │
        │   lifecycle      │              │     ...              │
        └──────────────────┘              └──────────────────────┘
```

The split: **screenshots on disk, metadata in Postgres.** This matches Bug-Fab's reference Python adapter and the `docs/PROTOCOL.md` recommendation. Storing PNGs as Postgres `bytea` works but bloats the table; the disk path is in the row instead.

---

## Backend setup

### 1. Confirm `@fastify/multipart` is registered

If your app already handles file uploads, multipart is already registered. Confirm in `apps/api/src/index.ts`:

```typescript
import multipart from '@fastify/multipart'
await app.register(multipart, { limits: { fileSize: 11 * 1024 * 1024 } })  // 11 MiB
```

The `fileSize` limit must be at least **11 MiB** (Bug-Fab's documented ceiling). If you already register multipart with a larger limit (e.g., for big uploads elsewhere), the larger limit covers Bug-Fab — do **not** double-register.

> **Why 11 MiB:** screenshot cap (10 MiB) + metadata JSON cap (256 KiB) + multipart envelope overhead. Set lower at your own risk; clients submitting from high-DPI displays may hit 10 MiB legitimately.

### 2. Install plugin scaffolding

Create `apps/api/src/plugins/bug-fab/` with the file structure from the [Fastify section of ADAPTERS.md](../ADAPTERS.md#fastify-typescript-fastify--5):

```
apps/api/src/plugins/bug-fab/
├── index.ts              ← public exports
├── plugin.ts             ← fp()-wrapped registration
├── types.ts              ← TypeScript types (copy from `repo/types/protocol.d.ts`)
├── validation.ts         ← magic-byte PNG check + enum validators
├── errors.ts             ← Errors.* factories matching the protocol envelope
├── github-sync.ts        ← best-effort GitHub Issues sync (optional)
├── storage.ts            ← DrizzleStorage (this guide § "Storage adapter")
└── routes/
    ├── submit.ts         ← POST /bug-reports handler
    └── viewer.ts         ← GET/PUT/DELETE /reports/* + bulk ops
```

The `ADAPTERS.md` Fastify section gives you the plugin shape; this guide adds the Drizzle storage and the deployment plumbing.

---

## Postgres schema

Two tables: `bug_reports` (one row per report, denormalized for fast listing) and `bug_report_lifecycle` (append-only audit log, cascade-deletes with the report).

`apps/api/src/db/schema/bug-reports.ts`:

```typescript
import {
  pgTable, pgEnum, text, timestamp, integer, jsonb, boolean,
} from 'drizzle-orm/pg-core'

export const bugReportSeverityEnum = pgEnum('bug_report_severity', [
  'low', 'medium', 'high', 'critical',
])

export const bugReportStatusEnum = pgEnum('bug_report_status', [
  'open', 'investigating', 'fixed', 'closed',
])

export const bugReportTypeEnum = pgEnum('bug_report_type', [
  'bug', 'feature_request',
])

export const bugReports = pgTable('bug_reports', {
  // bug-001 / bug-P038 / bug-D012 — see PROTOCOL.md §id format
  id:                       text('id').primaryKey(),
  protocolVersion:          text('protocol_version').notNull().default('0.1'),
  title:                    text('title').notNull(),
  reportType:               bugReportTypeEnum('report_type').notNull().default('bug'),
  description:              text('description').notNull().default(''),
  expectedBehavior:         text('expected_behavior').notNull().default(''),
  severity:                 bugReportSeverityEnum('severity').notNull().default('medium'),
  status:                   bugReportStatusEnum('status').notNull().default('open'),
  // Reporter sub-fields kept as separate columns for indexing / querying.
  // The 256-char caps come from PROTOCOL.md §reporter.
  reporterName:             text('reporter_name').notNull().default(''),
  reporterEmail:            text('reporter_email').notNull().default(''),
  reporterUserId:           text('reporter_user_id').notNull().default(''),
  // tags + the full context blob ride in jsonb. context.extra-keys preserved.
  tags:                     text('tags').array().notNull().default([]),
  context:                  jsonb('context').notNull().default({}),
  // User-Agent split — see PROTOCOL.md §User-Agent trust boundary.
  serverUserAgent:          text('server_user_agent').notNull().default(''),
  clientReportedUserAgent:  text('client_reported_user_agent'),
  // Screenshot path on disk (NOT bytea — see this guide § Architecture overview).
  screenshotPath:           text('screenshot_path').notNull(),
  // Optional GitHub linkage — both null until sync succeeds.
  githubIssueUrl:           text('github_issue_url'),
  githubIssueNumber:        integer('github_issue_number'),
  // Soft-archive (per Bug-Fab's bulk-archive-closed endpoint).
  archivedAt:               timestamp('archived_at', { withTimezone: true }),
  // Timestamps. `client_ts` is preserved as part of `context` round-trip.
  createdAt:                timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt:                timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
})

export const bugReportLifecycle = pgTable('bug_report_lifecycle', {
  id:             text('id').primaryKey(),
  bugReportId:    text('bug_report_id').notNull().references(() => bugReports.id, { onDelete: 'cascade' }),
  // 'created' | 'status_changed' | 'deleted' | 'archived'
  // (forward-additive — see PROTOCOL.md §Lifecycle.action)
  action:         text('action').notNull(),
  // 'anonymous' / null / a session userId — see PROTOCOL.md §Lifecycle.by
  by:             text('by').notNull().default('anonymous'),
  at:             timestamp('at', { withTimezone: true }).notNull().defaultNow(),
  // Only present when action == 'status_changed'.
  status:         bugReportStatusEnum('status'),
  fixCommit:      text('fix_commit').notNull().default(''),
  fixDescription: text('fix_description').notNull().default(''),
})
```

Generate and apply the migration:

```bash
cd apps/api
npm run db:generate    # creates drizzle/<timestamp>_add_bug_reports.sql
npm run db:migrate     # applies to your dev database
```

Review the generated SQL before applying to production. Drizzle creates the enums + tables in dependency order.

---

## Storage adapter (Drizzle)

`apps/api/src/plugins/bug-fab/storage.ts`:

```typescript
import { eq, and, isNull, sql, desc } from 'drizzle-orm'
import { writeFileSync, mkdirSync, existsSync, renameSync, rmSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import type { PostgresJsDatabase } from 'drizzle-orm/postgres-js'
import { bugReports, bugReportLifecycle } from '../../db/schema/bug-reports.js'
import type {
  IStorage, BugReportDetail, BugReportSummary, ListFilters,
  Status, BugReportListResponse,
} from './types.js'

interface DrizzleStorageOptions {
  db:             PostgresJsDatabase
  screenshotDir:  string                  // e.g., $STORAGE_DIR/bug-reports
  idPrefix?:      string                  // optional — produces "bug-P001" style ids
}

export class DrizzleStorage implements IStorage {
  constructor(private opts: DrizzleStorageOptions) {
    mkdirSync(resolve(opts.screenshotDir), { recursive: true })
  }

  // ID generation — max(id) scan is fine for low/moderate volume.
  // For high write throughput, switch to a Postgres SEQUENCE keyed on the
  // bug_reports table.
  private async nextId(): Promise<string> {
    const rows = await this.opts.db.select({ id: bugReports.id }).from(bugReports)
    let max = 0
    for (const row of rows) {
      const m = row.id.match(/(\d+)$/)
      if (m) { const n = parseInt(m[1]!, 10); if (n > max) max = n }
    }
    const padded = String(max + 1).padStart(3, '0')
    return `bug-${this.opts.idPrefix ?? ''}${padded}`
  }

  private screenshotPath(id: string): string {
    return join(resolve(this.opts.screenshotDir), `${id}.png`)
  }

  async saveReport(metadata: any, screenshotBytes: Buffer): Promise<string> {
    const id = await this.nextId()
    const now = new Date()
    const path = this.screenshotPath(id)

    // Atomic write: tmp + rename — same filesystem so rename is atomic.
    const tmp = path + `.tmp-${randomUUID()}`
    writeFileSync(tmp, screenshotBytes)
    renameSync(tmp, path)

    await this.opts.db.transaction(async (tx) => {
      await tx.insert(bugReports).values({
        id,
        protocolVersion:         metadata.protocol_version ?? '0.1',
        title:                   metadata.title,
        reportType:              metadata.report_type ?? 'bug',
        description:             metadata.description ?? '',
        expectedBehavior:        metadata.expected_behavior ?? '',
        severity:                metadata.severity ?? 'medium',
        status:                  'open',
        reporterName:            metadata.reporter?.name ?? '',
        reporterEmail:           metadata.reporter?.email ?? '',
        reporterUserId:          metadata.reporter?.user_id ?? '',
        tags:                    metadata.tags ?? [],
        // context preserves all extras (PROTOCOL.md says context allows extra keys)
        context:                 metadata.context ?? {},
        serverUserAgent:         metadata.server_user_agent ?? '',
        clientReportedUserAgent: metadata.context?.user_agent ?? null,
        screenshotPath:          path,
        createdAt:               now,
        updatedAt:               now,
      })
      await tx.insert(bugReportLifecycle).values({
        id:          randomUUID(),
        bugReportId: id,
        action:      'created',
        by:          'anonymous',  // override at the route layer if you have auth context
        at:          now,
      })
    })
    return id
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    const [row] = await this.opts.db
      .select().from(bugReports).where(eq(bugReports.id, id)).limit(1)
    if (!row) return null
    const lifecycle = await this.opts.db
      .select().from(bugReportLifecycle)
      .where(eq(bugReportLifecycle.bugReportId, id))
      .orderBy(bugReportLifecycle.at)
    return this.toDetail(row, lifecycle)
  }

  async listReports(
    filters: ListFilters, page: number, pageSize: number,
  ): Promise<BugReportListResponse> {
    const conds = []
    if (!filters.include_archived) conds.push(isNull(bugReports.archivedAt))
    if (filters.status)            conds.push(eq(bugReports.status,   filters.status))
    if (filters.severity)          conds.push(eq(bugReports.severity, filters.severity))
    const where = conds.length > 0 ? and(...conds) : undefined

    const [{ count }] = await this.opts.db
      .select({ count: sql<number>`count(*)::int` }).from(bugReports).where(where)
    const rows = await this.opts.db
      .select().from(bugReports).where(where)
      .orderBy(desc(bugReports.createdAt))
      .limit(pageSize).offset((page - 1) * pageSize)

    const stats = await this.opts.db
      .select({ status: bugReports.status, count: sql<number>`count(*)::int` })
      .from(bugReports).where(isNull(bugReports.archivedAt))
      .groupBy(bugReports.status)

    const statsMap = Object.fromEntries(stats.map(r => [r.status, r.count]))
    return {
      items: rows.map(this.toSummary),
      total: count ?? 0,
      page,
      page_size: pageSize,
      stats: {
        open:          statsMap['open']          ?? 0,
        investigating: statsMap['investigating'] ?? 0,
        fixed:         statsMap['fixed']         ?? 0,
        closed:        statsMap['closed']        ?? 0,
      },
    }
  }

  async getScreenshotPath(id: string): Promise<string | null> {
    const [row] = await this.opts.db
      .select({ p: bugReports.screenshotPath })
      .from(bugReports).where(eq(bugReports.id, id)).limit(1)
    if (!row || !existsSync(row.p)) return null
    return row.p
  }

  async updateStatus(
    id: string, newStatus: Status, by: string,
    fixCommit?: string, fixDescription?: string,
  ): Promise<BugReportDetail> {
    const now = new Date()
    await this.opts.db.transaction(async (tx) => {
      await tx.update(bugReports)
        .set({ status: newStatus, updatedAt: now })
        .where(eq(bugReports.id, id))
      await tx.insert(bugReportLifecycle).values({
        id:             randomUUID(),
        bugReportId:    id,
        action:         'status_changed',
        by,
        at:             now,
        status:         newStatus,
        fixCommit:      fixCommit ?? '',
        fixDescription: fixDescription ?? '',
      })
    })
    const fresh = await this.getReport(id)
    if (!fresh) throw new Error(`report ${id} disappeared during update`)
    return fresh
  }

  async deleteReport(id: string): Promise<void> {
    const [row] = await this.opts.db
      .select({ p: bugReports.screenshotPath })
      .from(bugReports).where(eq(bugReports.id, id)).limit(1)
    await this.opts.db.delete(bugReports).where(eq(bugReports.id, id))
    if (row?.p && existsSync(row.p)) rmSync(row.p, { force: true })
  }

  async archiveReport(id: string): Promise<void> {
    const now = new Date()
    await this.opts.db.transaction(async (tx) => {
      await tx.update(bugReports)
        .set({ archivedAt: now, updatedAt: now }).where(eq(bugReports.id, id))
      await tx.insert(bugReportLifecycle).values({
        id: randomUUID(), bugReportId: id, action: 'archived', by: 'system', at: now,
      })
    })
  }

  async bulkCloseFixed(): Promise<number> {
    const fixed = await this.opts.db
      .select({ id: bugReports.id }).from(bugReports)
      .where(and(eq(bugReports.status, 'fixed'), isNull(bugReports.archivedAt)))
    for (const { id } of fixed) await this.updateStatus(id, 'closed', 'system')
    return fixed.length
  }

  async bulkArchiveClosed(): Promise<number> {
    const closed = await this.opts.db
      .select({ id: bugReports.id }).from(bugReports)
      .where(and(eq(bugReports.status, 'closed'), isNull(bugReports.archivedAt)))
    for (const { id } of closed) await this.archiveReport(id)
    return closed.length
  }

  // Optional GitHub linkage update — duck-typed by the submit route.
  async setGitHubIssue(id: string, issueUrl: string, issueNumber: number): Promise<void> {
    await this.opts.db.update(bugReports)
      .set({ githubIssueUrl: issueUrl, githubIssueNumber: issueNumber })
      .where(eq(bugReports.id, id))
  }

  private toSummary = (row: any): BugReportSummary => ({
    id: row.id, title: row.title,
    report_type: row.reportType, severity: row.severity, status: row.status,
    module: (row.context?.module as string) ?? '',
    created_at: row.createdAt.toISOString(),
    has_screenshot: existsSync(row.screenshotPath),
    github_issue_url: row.githubIssueUrl,
  })

  private toDetail = (row: any, lifecycle: any[]): BugReportDetail => ({
    ...this.toSummary(row),
    description:                row.description,
    expected_behavior:          row.expectedBehavior,
    tags:                       row.tags,
    reporter: {
      name:    row.reporterName,
      email:   row.reporterEmail,
      user_id: row.reporterUserId,
    },
    context:                    row.context ?? {},
    lifecycle: lifecycle.map(l => ({
      action:           l.action,
      by:               l.by,
      at:               l.at.toISOString(),
      status:           l.status ?? undefined,
      fix_commit:       l.fixCommit,
      fix_description:  l.fixDescription,
    })),
    server_user_agent:          row.serverUserAgent,
    client_reported_user_agent: row.clientReportedUserAgent ?? '',
    environment:                row.context?.environment as string | undefined,
    client_ts:                  row.context?.client_ts as string | undefined,
    protocol_version:           row.protocolVersion,
    updated_at:                 row.updatedAt.toISOString(),
    github_issue_number:        row.githubIssueNumber,
  })
}
```

---

## Plugin registration

In `apps/api/src/index.ts`, after your existing `@fastify/multipart` registration:

```typescript
import { bugFab } from './plugins/bug-fab/plugin.js'
import { DrizzleStorage } from './plugins/bug-fab/storage.js'
import { db } from './db/client.js'

// (existing) await app.register(multipart, { limits: { fileSize: 11 * 1024 * 1024 } })

const bugStorage = new DrizzleStorage({
  db,
  screenshotDir: process.env.STORAGE_DIR
    ? `${process.env.STORAGE_DIR}/bug-reports`
    : './storage/bug-reports',
})

await app.register(bugFab, {
  storage:      bugStorage,
  submitPrefix: '/api',                    // → POST /api/bug-reports
  viewerPrefix: '/admin/bug-reports',      // → /admin/bug-reports/reports etc.
  github: process.env.BUG_FAB_GITHUB_ENABLED === 'true' ? {
    enabled: true,
    pat:     process.env.BUG_FAB_GITHUB_PAT!,
    repo:    process.env.BUG_FAB_GITHUB_REPO!,
  } : undefined,
  rateLimit: process.env.BUG_FAB_RATE_LIMIT_ENABLED === 'true' ? {
    enabled:     true,
    maxRequests: Number(process.env.BUG_FAB_RATE_LIMIT_MAX ?? 10),
    windowMs:    Number(process.env.BUG_FAB_RATE_LIMIT_WINDOW_MS ?? 3_600_000),
  } : undefined,
})
```

Remember the [viewer mount-prefix invariant](../ADAPTERS.md#viewer-mount-prefix-note): `viewerPrefix` cannot be `/` or empty. Pick something like `/admin`, `/admin/bug-reports`, or `/internal/feedback`.

---

## Auth (onRequest hook)

Bug-Fab v0.1 ships no auth abstraction. Protect viewer routes via Fastify's `onRequest` hook **before** registering the plugin:

```typescript
// Before bugFab plugin registration:
app.addHook('onRequest', async (req, reply) => {
  if (req.url.startsWith('/admin/bug-reports')) {
    if (!req.session?.userId) {
      return reply.status(401).send({
        error:  'unauthorized',
        detail: 'Sign in to view bug reports.',
      })
    }
    // Optional: role check
    if (req.session.role !== 'admin') {
      return reply.status(403).send({
        error:  'forbidden',
        detail: 'Admin role required.',
      })
    }
  }
})
```

The intake endpoint (`POST /api/bug-reports`) is left open so unauthenticated end-users can file bug reports. If you want to require auth for intake too, expand the URL prefix check or move the hook to `addHook('preHandler', ...)` per-route.

### Lifecycle `by` field with real users

If you want lifecycle entries to record the actual user who changed a status (instead of the `'anonymous'` default), pass the session userId from the Fastify route handler into `storage.updateStatus(id, status, by, ...)`. The Drizzle storage already accepts a `by` parameter; the plugin's viewer routes should extract it from `req.session.userId` rather than hardcoding `'api'` or `'system'`.

---

## Frontend setup

### 1. Copy the static bundle into `public/`

The Bug-Fab static bundle (`bug-fab.js` + vendored `vendor/html2canvas.min.js`) ships from the Bug-Fab repo:

```bash
# Once, from your Next.js app root
mkdir -p apps/web/public/bug-fab
curl -L https://raw.githubusercontent.com/AZgeekster/Bug-Fab/main/static/bug-fab.js \
  -o apps/web/public/bug-fab/bug-fab.js
mkdir -p apps/web/public/bug-fab/vendor
curl -L https://raw.githubusercontent.com/AZgeekster/Bug-Fab/main/static/vendor/html2canvas.min.js \
  -o apps/web/public/bug-fab/vendor/html2canvas.min.js
```

You can also pin to a specific Bug-Fab tag (e.g., `https://raw.githubusercontent.com/AZgeekster/Bug-Fab/v0.1.0/static/bug-fab.js`) for reproducibility.

### 2. Inject the script in the root layout

`apps/web/src/app/layout.tsx`:

```tsx
import Script from 'next/script'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <Script
          src="/bug-fab/bug-fab.js"
          strategy="afterInteractive"
        />
        <Script id="bug-fab-init" strategy="afterInteractive">{`
          window.addEventListener("DOMContentLoaded", () => {
            if (!window.BugFab) return;
            window.BugFab.init({
              submitUrl:        '${process.env.NEXT_PUBLIC_API_BASE ?? ""}/api/bug-reports',
              html2canvasUrl:   '/bug-fab/vendor/html2canvas.min.js',
              appVersion:       '${process.env.NEXT_PUBLIC_APP_VERSION ?? "dev"}',
              environment:      '${process.env.NODE_ENV}',
            });
          });
        `}</Script>
      </body>
    </html>
  )
}
```

Notes:

- The `Script` component must be in the **server-component layout** (`app/layout.tsx`), not in any layout that has `'use client'`. Client-component layouts can't render `Script`.
- `strategy="afterInteractive"` is the right choice — the bundle is non-critical and shouldn't block first paint.
- `window.BugFab` is namespace-protected; the `if (!window.BugFab) return;` guard prevents a load-order race where the init runs before the bundle finishes loading.
- The init payload is **plain template-literal interpolation**. If `NEXT_PUBLIC_API_BASE` contains a quote or backtick, it'll inject. In practice these env vars are set by your deploy pipeline and are safe; but if you're paranoid, JSON.stringify them via a small helper.

### 3. Optional: client-component init

If your root layout is already a client component (you have `'use client'` at the top, which is uncommon for the root), the same effect via `useEffect`:

```tsx
'use client'
import { useEffect } from 'react'

function BugFabInit() {
  useEffect(() => {
    const script = document.createElement('script')
    script.src = '/bug-fab/bug-fab.js'
    script.defer = true
    script.onload = () => {
      window.BugFab?.init({
        submitUrl:      `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/bug-reports`,
        html2canvasUrl: '/bug-fab/vendor/html2canvas.min.js',
      })
    }
    document.body.appendChild(script)
    return () => { script.remove() }
  }, [])
  return null
}
```

---

## PM2 deployment

`ecosystem.config.js`:

```javascript
module.exports = {
  apps: [
    {
      name:        'api',
      script:      'apps/api/dist/index.js',
      instances:   1,                    // see Multi-process warning below
      exec_mode:   'fork',
      autorestart: true,
      max_memory_restart: '500M',
      env: {
        NODE_ENV:                       'production',
        STORAGE_DIR:                    '/var/lib/your-app/storage',
        BUG_FAB_GITHUB_ENABLED:         'true',
        BUG_FAB_GITHUB_REPO:            'your-org/bug-tracker',
        // BUG_FAB_GITHUB_PAT comes from your secret manager — DO NOT put PATs in this file
        BUG_FAB_RATE_LIMIT_ENABLED:     'true',
        BUG_FAB_RATE_LIMIT_MAX:         '10',
        BUG_FAB_RATE_LIMIT_WINDOW_MS:   '3600000',
      },
    },
    {
      name:        'web',
      script:      'npm',
      args:        'start --workspace=apps/web',
      instances:   1,
      exec_mode:   'fork',
      autorestart: true,
    },
  ],
}
```

Boot:

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup     # generates a systemd unit so PM2 restarts on reboot
```

### Multi-process warning

If you're tempted to set `instances: 'max'` (cluster mode):

- The DrizzleStorage above is **multi-process safe** for metadata (Postgres handles concurrency). The `nextId()` `max(id)` scan is racy under cluster mode — switch to a Postgres `SEQUENCE` for the ID source if you go multi-instance.
- The in-memory rate limiter is **per-process**. Either use a reverse-proxy rate limiter (nginx, Cloudflare) for the real boundary, or switch the rate limiter to Redis-backed.
- Screenshot writes are atomic (tmp + rename) and don't conflict — the filesystem handles that.

For most small/medium deployments, `instances: 1` is fine. Cluster mode is an optimization, not a requirement.

### Persistent volume

`STORAGE_DIR/bug-reports/` must be on a persistent volume that survives:

- PM2 restart
- Server reboot
- Container redeploy (in containerized deployments — mount the dir as a Docker volume, K8s PVC, etc.)

Common paths: `/var/lib/your-app/storage/bug-reports/`. Avoid `/tmp` (cleaned on reboot on most distros) and avoid the app's working directory if your deploy strategy wipes it on every release.

### Backup

Two artifacts to back up:

1. **PostgreSQL** — `pg_dump` covers the metadata. Existing backup pipelines for your app's database already cover this.
2. **Screenshot directory** — `tar` the `bug-reports/` dir. Consider rsync to a backup server, S3 sync, or whatever your team uses for static assets.

A bug report is partially recoverable from just the database (metadata only, no image) or just the disk (image only, no metadata). Both together for full recovery.

---

## Configuration & environment variables

| Variable | Required? | Default | Purpose |
|----------|-----------|---------|---------|
| `STORAGE_DIR` | yes | `./storage` | Parent dir; screenshots go in `$STORAGE_DIR/bug-reports/`. |
| `DATABASE_URL` | yes | — | Your existing app's Postgres URL. |
| `NEXT_PUBLIC_API_BASE` | optional | `""` | Frontend uses this to build the absolute submit URL. Leave empty for same-origin. |
| `NEXT_PUBLIC_APP_VERSION` | optional | `"dev"` | Surfaced in `context.app_version` of every report. |
| `BUG_FAB_GITHUB_ENABLED` | optional | `false` | Turn on GitHub Issues sync. |
| `BUG_FAB_GITHUB_PAT` | when sync on | — | GitHub Personal Access Token with `repo` scope (private) or `public_repo` (public). |
| `BUG_FAB_GITHUB_REPO` | when sync on | — | `owner/repo` for the issue tracker. |
| `BUG_FAB_RATE_LIMIT_ENABLED` | optional | `false` | Turn on per-IP rate limiting. |
| `BUG_FAB_RATE_LIMIT_MAX` | optional | `10` | Max submissions per window per IP. |
| `BUG_FAB_RATE_LIMIT_WINDOW_MS` | optional | `3600000` (1 hr) | Sliding-window length in ms. |

---

## Conformance verification

Once the integration is live (locally or on staging), run Bug-Fab's conformance suite to confirm protocol compliance:

```bash
# Install Bug-Fab Python package (only the test runner is Python).
pip install --pre bug-fab

# Boot your Fastify app on a known port (locally: npm run dev:api).
# In a separate terminal:
pytest --bug-fab-conformance --base-url=http://localhost:3000

# CI-friendly: skip mutating tests if running against shared infrastructure.
pytest --bug-fab-conformance --base-url=http://localhost:3000 --skip-mutating
```

The suite covers:

- Submit happy path (valid metadata + PNG → 201 + stored).
- Severity / status / report_type strict rejection (no silent coercion).
- Protocol version handshake (`400 unsupported_protocol_version` for unknown versions).
- Magic-byte PNG check (JPEG → 415).
- Multipart size limits (oversize → 413).
- Lifecycle log shape and append-only behavior.
- Bulk operations (correct counts; no-op on already-target-state).
- Deprecated-values rule (storing `status: "resolved"` and reading it back without rejection).

A failure in any test means your adapter has drifted from the protocol; fix and re-run.

---

## Operations

### Monitoring

A few metrics worth scraping into your observability stack:

- **Submit rate** (`POST /api/bug-reports` count). Sudden spikes mean either a new bug ships or something is auto-submitting.
- **Rate-limit denials** (`429` count). If routinely high, the limit is too tight.
- **Storage dir size**. Bug-Fab doesn't garbage-collect old reports; grow the alert threshold accordingly.
- **GitHub sync failure rate**. Sync is best-effort, but a 100% failure rate means a stale PAT or wrong repo.

### Pruning old reports

Bug-Fab v0.1 has no built-in retention policy. If you accumulate too many old reports:

```sql
-- Find archived reports older than 90 days:
SELECT id FROM bug_reports
WHERE archived_at IS NOT NULL AND archived_at < NOW() - INTERVAL '90 days';

-- Delete them (cascade-deletes lifecycle rows too):
DELETE FROM bug_reports
WHERE archived_at IS NOT NULL AND archived_at < NOW() - INTERVAL '90 days'
RETURNING id, screenshot_path;
```

Then `rm` the returned `screenshot_path` files. A weekly cron job or a [bulk-archive workflow](#bulk-operations) keeps the dataset trimmed.

### Bulk operations

The viewer ships `POST /admin/bug-reports/bulk-close-fixed` and `POST /admin/bug-reports/bulk-archive-closed` for periodic housekeeping. Wire them into a scheduled job (cron, GitHub Action, etc.) if you want automated triage.

---

## Common gotchas

1. **Mount-prefix collision.** `viewerPrefix: '/'` throws at startup — see [`ADAPTERS.md` § Viewer mount-prefix note](../ADAPTERS.md#viewer-mount-prefix-note). The viewer's HTML list lives at the prefix root.
2. **Multi-instance ID collisions.** If you run multiple Fastify processes (PM2 cluster mode, multiple containers), the `max(id)` scan races. Switch to a Postgres `SEQUENCE`. Symptom: occasional duplicate-key errors on insert.
3. **`Content-Type: application/json` on intake.** Bug-Fab is `multipart/form-data`. If you're seeing 400s on submit and the request body looks like JSON, your client is misconfigured — check the bundle's network panel for the actual outgoing request.
4. **Browser cache stickiness.** When you update `bug-fab.js`, browsers may serve a cached copy. Either version the URL (`bug-fab.js?v=2`), set a short `Cache-Control` on the static handler, or use a CDN with cache invalidation.
5. **`onRequest` order matters.** The auth `addHook` must run **before** `app.register(bugFab, ...)`. Hooks added after the plugin won't fire for plugin routes (Fastify encapsulation rules; even with fp-wrapping, registration order matters).
6. **TKR-style `ok()`/`fail()` envelope.** Don't wrap Bug-Fab responses in your app's standard envelope. The protocol uses bare JSON for success and `{ error, detail }` for failures. Wrapping breaks conformance.
7. **GitHub PAT with insufficient scope.** A PAT without `repo` scope will silently fail to create issues — you'll see the report saved locally but `github_issue_url: null`. Check the PAT scopes if sync isn't working.
8. **Next.js `'use client'` in the root layout.** The `<Script>` component requires a server-component layout. If your root layout is `'use client'`, switch to the `useEffect` pattern in [Frontend setup §3](#3-optional-client-component-init).

---

## Upgrade path

When Bug-Fab's wire protocol bumps (e.g., to v0.2):

1. **Read the changelog** — `repo/CHANGELOG.md` documents the breaking changes.
2. **Diff `protocol-schema.json`** — the JSON Schema is authoritative; field shape changes show up there before anywhere else.
3. **Update `apps/api/src/plugins/bug-fab/types.ts`** to match the new schema. The hand-maintained snapshot at `repo/types/protocol.d.ts` is the canonical TypeScript shape.
4. **Update validation** — new required fields → add to `validateSubmission`. Removed fields → drop the checks.
5. **Migration** — if the protocol changes the wire-storage shape (e.g., a renamed field), write a Drizzle migration that backfills the new column from the old one. Bug-Fab promises that **deprecated values stay legal-on-read forever**, so you don't have to rewrite history; just translate on the way in / out.
6. **Re-run conformance** against the upgraded plugin. Conformance tests pin the new protocol version.

The Bug-Fab maintainer publishes a migration guide for each protocol bump under `repo/docs/UPGRADE.md`.

---

## References

- **Wire protocol contract:** [`docs/PROTOCOL.md`](../PROTOCOL.md) + [`docs/protocol-schema.json`](../protocol-schema.json) (authoritative).
- **TypeScript types:** [`types/protocol.d.ts`](../../types/protocol.d.ts).
- **Adapter sketch (Fastify):** [`docs/ADAPTERS.md` § Fastify](../ADAPTERS.md#fastify-typescript-fastify--5).
- **Conformance suite docs:** [`docs/CONFORMANCE.md`](../CONFORMANCE.md).
- **AI-coder companion:** [`fastify-nextjs-postgres.AGENTS.md`](./fastify-nextjs-postgres.AGENTS.md) — file-by-file prescriptive instructions for AI assistants performing the integration.
- **Adapters registry:** [`docs/ADAPTERS_REGISTRY.md`](../ADAPTERS_REGISTRY.md).
- **First consumer to validate this guide:** TKR (Andrew's other project) — first Bug-Fab Node consumer, surfaced the spec-tightening pass that made the v0.1 wire protocol stable.
