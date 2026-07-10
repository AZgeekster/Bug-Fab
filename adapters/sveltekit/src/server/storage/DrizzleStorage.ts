// DrizzleStorage — Drizzle ORM-backed storage example.
//
// This is illustrative. Consumers wire up their own Drizzle instance and pass
// it in. The adapter does NOT pull Drizzle as a dependency — it's typed as
// `unknown` at the boundary so this file can compile without drizzle-orm
// installed in the package devDeps.
//
// Why ship this: the existing SvelteKit sketch in repo/docs/ADAPTERS.md uses
// Drizzle, and the SvelteKit + serverless deploy story (Vercel, Cloudflare
// Pages with D1, Neon Postgres, libsql) is all Drizzle-friendly.
//
// To use: `pnpm add drizzle-orm`, define schema below, instantiate, pass to
// the handler factories. See README § "DrizzleStorage example".

/* eslint-disable @typescript-eslint/no-explicit-any */
import type {
  IStorage,
  SaveReportInput,
  BugReportDetail,
  BugReportSummary,
  BugReportListStats,
  ListFilters,
  Status,
  LifecycleEvent
} from '../types.js';

// We type the Drizzle DB and table objects as `any` so this file compiles
// even when drizzle-orm is not installed. Consumers re-export their typed
// `db` and table definitions; the runtime calls work because Drizzle's
// fluent API matches across versions.
export interface DrizzleStorageOptions {
  /** Drizzle DB instance (drizzle-orm). */
  db: any;
  /** Table objects defined in the consumer's schema. */
  tables: {
    bugReports: any;
    bugReportLifecycle: any;
  };
  /** Drizzle's `eq` helper, imported by the consumer. */
  eq: (a: any, b: any) => any;
  /** Where to read/write screenshot blobs. The wire protocol stores PNGs
   *  out-of-band by default; consumers typically use object storage (S3/R2)
   *  or the database's BYTEA column. This adapter delegates both. */
  screenshotIO: {
    write(id: string, bytes: Uint8Array): Promise<string>; // returns storedAt URI
    read(id: string): Promise<Uint8Array | null>;
    delete(id: string): Promise<void>;
    locate(id: string): Promise<string | null>; // returns local file path if applicable, else null
  };
  idPrefix?: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function pad(n: number, width = 3): string {
  return String(n).padStart(width, '0');
}

/**
 * Reference Drizzle schema (commented for the sketch — consumers paste this
 * into their own `schema.ts`):
 *
 * ```ts
 * import { pgTable, text, timestamp, jsonb, integer, boolean } from 'drizzle-orm/pg-core';
 *
 * export const bugReports = pgTable('bug_reports', {
 *   id:                    text('id').primaryKey(),
 *   title:                 text('title').notNull(),
 *   description:           text('description').notNull().default(''),
 *   expectedBehavior:      text('expected_behavior'),
 *   reportType:            text('report_type').notNull().default('bug'),
 *   severity:              text('severity').notNull().default('medium'),
 *   status:                text('status').notNull().default('open'),
 *   tags:                  jsonb('tags').$type<string[]>().notNull().default([]),
 *   reporter:              jsonb('reporter').$type<Record<string, string>>().notNull().default({}),
 *   context:               jsonb('context').$type<Record<string, unknown>>().notNull().default({}),
 *   clientTs:              text('client_ts').notNull(),
 *   protocolVersion:       text('protocol_version').notNull(),
 *   createdAt:             timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
 *   updatedAt:             timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
 *   archivedAt:            timestamp('archived_at', { withTimezone: true }),
 *   serverUserAgent:       text('server_user_agent').notNull().default(''),
 *   clientReportedUserAgent: text('client_reported_user_agent'),
 *   screenshotRef:         text('screenshot_ref').notNull(),
 *   githubIssueUrl:        text('github_issue_url'),
 *   githubIssueNumber:     integer('github_issue_number'),
 * });
 *
 * export const bugReportLifecycle = pgTable('bug_report_lifecycle', {
 *   id:             text('id').primaryKey(),
 *   bugReportId:    text('bug_report_id').notNull(),
 *   action:         text('action').notNull(),
 *   by:             text('by'),
 *   at:             timestamp('at', { withTimezone: true }).notNull().defaultNow(),
 *   status:         text('status'),
 *   fixCommit:      text('fix_commit'),
 *   fixDescription: text('fix_description'),
 * });
 * ```
 */
export class DrizzleStorage implements IStorage {
  private readonly opts: DrizzleStorageOptions;
  private readonly prefix: string;
  private counter = 0;
  private counterLoaded = false;

  constructor(opts: DrizzleStorageOptions) {
    this.opts = opts;
    this.prefix = opts.idPrefix ?? '';
  }

  /**
   * Resolves the next free id. Reads max(id) from DB on first call, then
   * increments a process-local counter.
   *
   * NOT production-safe, and this is a sketch (the `db` is typed at the
   * boundary and this file ships without a live drizzle dependency), so the
   * counter below is illustrative only. Two failure modes to design out in a
   * real integration:
   *
   * 1. **Reuse after delete + restart.** The counter re-seeds from `max(id)`
   *    on the next process start, so deleting the highest report and restarting
   *    reissues that id — the protocol says ids are never reused.
   * 2. **Concurrency.** `counter++` races across concurrent inserts.
   *
   * The runnable adapters (Python reference, Phoenix, Rails, Vapor, Spring,
   * ASP.NET) all fix both with a single-row counter table bumped by an atomic
   * `UPDATE bug_fab_id_counter SET last_value = last_value + 1` inside the
   * insert transaction (never `SELECT ... FOR UPDATE`, a SQLite syntax error).
   * In drizzle that is a raw `sql\`...\`` statement returning the new value; a
   * database sequence / identity column is the other standard option. Do one of
   * those before shipping.
   */
  private async nextId(): Promise<string> {
    if (!this.counterLoaded) {
      this.counterLoaded = true;
      try {
        const rows: { id: string }[] = await this.opts.db.select({ id: this.opts.tables.bugReports.id }).from(this.opts.tables.bugReports);
        for (const row of rows) {
          const m = row.id.match(/(\d+)$/);
          if (m) {
            const n = parseInt(m[1]!, 10);
            if (n > this.counter) this.counter = n;
          }
        }
      } catch {
        // Empty table or DB unreachable.
      }
    }
    this.counter++;
    const n = pad(this.counter, Math.max(3, String(this.counter).length));
    return this.prefix ? `bug-${this.prefix}${n}` : `bug-${n}`;
  }

  async saveReport(input: SaveReportInput): Promise<{ id: string; storedAt: string; receivedAt: string }> {
    const { submission, serverUserAgent, clientReportedUserAgent, screenshotBytes } = input;
    const id = await this.nextId();
    const receivedAt = nowIso();

    const screenshotRef = await this.opts.screenshotIO.write(id, screenshotBytes);

    await this.opts.db.insert(this.opts.tables.bugReports).values({
      id,
      title: submission.title,
      description: submission.description ?? '',
      // Schema defaults these string fields to "" — store "" rather than
      // null so the wire shape matches on read-back (audit A1).
      expectedBehavior: submission.expected_behavior ?? '',
      reportType: submission.report_type ?? 'bug',
      severity: submission.severity ?? 'medium',
      status: 'open',
      tags: submission.tags ?? [],
      reporter: submission.reporter ?? {},
      context: submission.context ?? {},
      clientTs: submission.client_ts,
      protocolVersion: submission.protocol_version,
      createdAt: new Date(receivedAt),
      updatedAt: new Date(receivedAt),
      archivedAt: null,
      serverUserAgent,
      clientReportedUserAgent: clientReportedUserAgent ?? '',
      screenshotRef,
      githubIssueUrl: null,
      githubIssueNumber: null
    });

    await this.opts.db.insert(this.opts.tables.bugReportLifecycle).values({
      id: `${id}:created`,
      bugReportId: id,
      action: 'created',
      by: 'anonymous',
      at: new Date(receivedAt),
      status: 'open',
      // Schema default for these fields is "" — store "" rather than null
      // so the wire shape matches when reading back.
      fixCommit: '',
      fixDescription: ''
    });

    return { id, storedAt: screenshotRef, receivedAt };
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    const rows = await this.opts.db
      .select()
      .from(this.opts.tables.bugReports)
      .where(this.opts.eq(this.opts.tables.bugReports.id, id));
    const row = rows[0];
    if (!row) return null;

    const lifecycle = await this.opts.db
      .select()
      .from(this.opts.tables.bugReportLifecycle)
      .where(this.opts.eq(this.opts.tables.bugReportLifecycle.bugReportId, id));

    return rowToDetail(row, lifecycle);
  }

  async listReports(
    filters: ListFilters,
    page: number,
    pageSize: number
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }> {
    // For brevity, this draft fetches all matching rows then paginates in
    // memory. Production implementations should push the WHERE / LIMIT down.
    let rows: any[] = await this.opts.db.select().from(this.opts.tables.bugReports);

    if (!filters.include_archived) rows = rows.filter((r) => !r.archivedAt);
    if (filters.status) rows = rows.filter((r) => r.status === filters.status);
    if (filters.severity) rows = rows.filter((r) => r.severity === filters.severity);
    if (filters.environment) {
      rows = rows.filter((r) => (r.context as Record<string, unknown> | null)?.environment === filters.environment);
    }

    const stats: BugReportListStats = {
      open: rows.filter((r) => r.status === 'open').length,
      investigating: rows.filter((r) => r.status === 'investigating').length,
      fixed: rows.filter((r) => r.status === 'fixed').length,
      closed: rows.filter((r) => r.status === 'closed').length
    };

    rows.sort((a, b) => (b.createdAt as Date).getTime() - (a.createdAt as Date).getTime());

    const total = rows.length;
    const slice = rows.slice((page - 1) * pageSize, page * pageSize);
    const items = slice.map(rowToSummary);
    return { items, total, stats };
  }

  async getScreenshotPath(id: string): Promise<string | null> {
    return this.opts.screenshotIO.locate(id);
  }

  async getScreenshotBytes(id: string): Promise<Uint8Array | null> {
    return this.opts.screenshotIO.read(id);
  }

  async updateStatus(
    id: string,
    newStatus: Status,
    by: string | null,
    fixCommit?: string,
    fixDescription?: string
  ): Promise<BugReportDetail> {
    const at = new Date();
    await this.opts.db
      .update(this.opts.tables.bugReports)
      .set({ status: newStatus, updatedAt: at })
      .where(this.opts.eq(this.opts.tables.bugReports.id, id));
    await this.opts.db.insert(this.opts.tables.bugReportLifecycle).values({
      id: `${id}:status:${at.toISOString()}`,
      bugReportId: id,
      action: 'status_changed',
      // Schema default for `by`/`fixCommit`/`fixDescription` is "" — store
      // "" rather than null so the wire shape matches on read-back (audit A1).
      by: by ?? '',
      at,
      status: newStatus,
      fixCommit: fixCommit ?? '',
      fixDescription: fixDescription ?? ''
    });
    const detail = await this.getReport(id);
    if (!detail) throw new Error(`Report not found after update: ${id}`);
    return detail;
  }

  async deleteReport(id: string): Promise<void> {
    await this.opts.db
      .delete(this.opts.tables.bugReportLifecycle)
      .where(this.opts.eq(this.opts.tables.bugReportLifecycle.bugReportId, id));
    await this.opts.db
      .delete(this.opts.tables.bugReports)
      .where(this.opts.eq(this.opts.tables.bugReports.id, id));
    await this.opts.screenshotIO.delete(id);
  }

  async archiveReport(id: string): Promise<void> {
    const at = new Date();
    await this.opts.db
      .update(this.opts.tables.bugReports)
      .set({ archivedAt: at, updatedAt: at })
      .where(this.opts.eq(this.opts.tables.bugReports.id, id));
    await this.opts.db.insert(this.opts.tables.bugReportLifecycle).values({
      id: `${id}:archived:${at.toISOString()}`,
      bugReportId: id,
      action: 'archived',
      by: 'system',
      at,
      fixCommit: '',
      fixDescription: ''
    });
  }

  async bulkCloseFixed(): Promise<number> {
    const rows: any[] = await this.opts.db.select().from(this.opts.tables.bugReports);
    const fixed = rows.filter((r) => r.status === 'fixed' && !r.archivedAt);
    for (const r of fixed) {
      await this.updateStatus(r.id, 'closed', 'system');
    }
    return fixed.length;
  }

  async bulkArchiveClosed(): Promise<number> {
    const rows: any[] = await this.opts.db.select().from(this.opts.tables.bugReports);
    const closed = rows.filter((r) => r.status === 'closed' && !r.archivedAt);
    for (const r of closed) {
      await this.archiveReport(r.id);
    }
    return closed.length;
  }

  async setGitHubIssue(id: string, issueUrl: string, issueNumber: number): Promise<void> {
    await this.opts.db
      .update(this.opts.tables.bugReports)
      .set({ githubIssueUrl: issueUrl, githubIssueNumber: issueNumber })
      .where(this.opts.eq(this.opts.tables.bugReports.id, id));
  }
}

// --- Row-to-wire mapping helpers (Drizzle camelCase → wire snake_case) ---

// Wire-shape emitters: every field declared `string` with `default: ""` in
// the JSON Schema MUST be a real string on the wire (never undefined). The
// coalescing-to-"" pattern below preserves that invariant when Drizzle
// columns are nullable (which Postgres / SQLite often are by default).
// See audit 2026-05-01 § A1.
function strOr(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

function rowToSummary(r: any): BugReportSummary {
  const ctx = (r.context as Record<string, unknown> | null) ?? {};
  return {
    id: r.id,
    title: r.title,
    report_type: r.reportType,
    severity: r.severity,
    status: r.status,
    module: strOr(ctx.module),
    created_at: (r.createdAt as Date).toISOString(),
    has_screenshot: true,
    github_issue_url: r.githubIssueUrl ?? null
  };
}

function rowToDetail(r: any, lifecycleRows: any[]): BugReportDetail {
  const lifecycle: LifecycleEvent[] = lifecycleRows.map((row) => {
    const event: LifecycleEvent = {
      action: row.action,
      by: strOr(row.by),
      at: (row.at as Date).toISOString(),
      fix_commit: strOr(row.fixCommit),
      fix_description: strOr(row.fixDescription)
    };
    if (row.status) event.status = row.status;
    return event;
  });

  const ctx = (r.context as Record<string, unknown> | null) ?? {};

  return {
    id: r.id,
    title: r.title,
    description: strOr(r.description),
    expected_behavior: strOr(r.expectedBehavior),
    report_type: r.reportType,
    severity: r.severity,
    status: r.status,
    module: strOr(ctx.module),
    created_at: (r.createdAt as Date).toISOString(),
    updated_at: (r.updatedAt as Date).toISOString(),
    has_screenshot: true,
    github_issue_url: r.githubIssueUrl ?? null,
    github_issue_number: r.githubIssueNumber ?? null,
    tags: (r.tags as string[]) ?? [],
    reporter: (r.reporter as Record<string, string>) ?? {},
    context: ctx,
    lifecycle,
    server_user_agent: strOr(r.serverUserAgent),
    client_reported_user_agent: strOr(r.clientReportedUserAgent),
    environment: strOr(ctx.environment),
    client_ts: strOr(r.clientTs),
    protocol_version: strOr(r.protocolVersion) || '0.1'
  };
}
