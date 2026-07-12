// FileStorage — zero-dependency file-system storage for the SvelteKit adapter.
//
// Layout: <storageDir>/<reportId>/metadata.json + screenshot.png
// Atomic writes via temp-file-then-rename. In-memory index for filtering.
//
// Concurrency: NOT safe for multi-process deployment. The in-memory ID counter
// will collide between Node workers. For multi-worker SvelteKit deployments
// (or serverless), use DrizzleStorage against a real DB instead.
//
// Adapter compatibility: requires a Node-style filesystem. Compatible with
// @sveltejs/adapter-node and Bun. NOT compatible with Cloudflare Workers,
// Vercel Edge, or other serverless runtimes that lack `fs` — use
// DrizzleStorage with libsql/Postgres for those.

import { promises as fs } from 'node:fs';
import { existsSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import type {
  IStorage,
  SaveReportInput,
  StoredReport,
  BugReportDetail,
  BugReportSummary,
  BugReportListStats,
  ListFilters,
  Status,
  LifecycleEvent
} from '../types.js';

export interface FileStorageOptions {
  storageDir: string;
  /** e.g. "P" yields ids like "bug-P001". Useful for multi-environment shared collectors. */
  idPrefix?: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function pad(n: number, width = 3): string {
  return String(n).padStart(width, '0');
}

// Wire-shape emitters: every field that the JSON Schema declares as
// `string` with `default: ""` MUST be a real string on the wire (never
// undefined). JSON.stringify drops undefined values, which would silently
// omit fields the schema requires. See audit 2026-05-01 § A1.
function moduleOf(r: StoredReport): string {
  return typeof r.context?.module === 'string' ? r.context.module : '';
}

function environmentOf(r: StoredReport): string {
  return typeof r.context?.environment === 'string' ? r.context.environment : '';
}

function summaryFrom(r: StoredReport): BugReportSummary {
  return {
    id: r.id,
    title: r.title,
    report_type: r.report_type,
    severity: r.severity,
    status: r.status,
    module: moduleOf(r),
    created_at: r.created_at,
    has_screenshot: true,
    github_issue_url: r.github_issue_url
  };
}

function detailFrom(r: StoredReport): BugReportDetail {
  return {
    id: r.id,
    title: r.title,
    description: r.description ?? '',
    expected_behavior: r.expected_behavior ?? '',
    report_type: r.report_type,
    severity: r.severity,
    status: r.status,
    module: moduleOf(r),
    created_at: r.created_at,
    updated_at: r.updated_at ?? '',
    has_screenshot: true,
    github_issue_url: r.github_issue_url,
    github_issue_number: r.github_issue_number,
    tags: r.tags ?? [],
    reporter: r.reporter ?? {},
    context: r.context ?? {},
    lifecycle: r.lifecycle,
    server_user_agent: r.server_user_agent ?? '',
    client_reported_user_agent: r.client_reported_user_agent ?? '',
    environment: environmentOf(r),
    client_ts: r.client_ts ?? '',
    protocol_version: r.protocol_version
  };
}

export class FileStorage implements IStorage {
  private readonly dir: string;
  private readonly prefix: string;
  private counter = 0;
  private index = new Map<string, StoredReport>();
  private loaded = false;

  constructor(opts: FileStorageOptions) {
    this.dir = resolve(opts.storageDir);
    this.prefix = opts.idPrefix ?? '';
    // mkdirSync is fine in the constructor — it's a one-shot setup.
    mkdirSync(this.dir, { recursive: true });
  }

  private async ensureLoaded(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    await this.loadFrom(this.dir, 'archive');
    // Archived reports load too: they must stay reachable via
    // include_archived=true, and — critically — they must keep seeding the
    // ID counter. Skipping archive/ meant a restart could re-mint an
    // archived report's id, and a later archive of the reused id would
    // overwrite the original's files.
    await this.loadFrom(join(this.dir, 'archive'));
  }

  private async loadFrom(root: string, skipName?: string): Promise<void> {
    try {
      const entries = await fs.readdir(root, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory() || entry.name === skipName) continue;
        const metaPath = join(root, entry.name, 'metadata.json');
        try {
          const raw = await fs.readFile(metaPath, 'utf8');
          const r = JSON.parse(raw) as StoredReport;
          this.index.set(r.id, r);
          const match = r.id.match(/(\d+)$/);
          if (match) {
            const n = parseInt(match[1]!, 10);
            if (n > this.counter) this.counter = n;
          }
        } catch {
          // Corrupt or missing metadata — skip.
        }
      }
    } catch {
      // Directory not yet readable.
    }
  }

  private nextId(): string {
    this.counter++;
    const n = pad(this.counter, Math.max(3, String(this.counter).length));
    return this.prefix ? `bug-${this.prefix}${n}` : `bug-${n}`;
  }

  private reportDir(id: string): string {
    return join(this.dir, id);
  }
  /** Resolve a report's on-disk directory — archived reports live under archive/. */
  private dirFor(r: StoredReport): string {
    return r.archived_at ? join(this.dir, 'archive', r.id) : this.reportDir(r.id);
  }
  private screenshotFile(id: string): string {
    return join(this.reportDir(id), 'screenshot.png');
  }

  private async writeMeta(report: StoredReport, dir?: string): Promise<void> {
    const dest = join(dir ?? this.dirFor(report), 'metadata.json');
    const tmp = `${dest}.tmp-${randomUUID()}`;
    await fs.writeFile(tmp, JSON.stringify(report, null, 2), 'utf8');
    await fs.rename(tmp, dest);
    // Archived reports stay in the index (with archived_at set) so they
    // remain reachable via include_archived and keep seeding the counter.
    this.index.set(report.id, report);
  }

  async saveReport(input: SaveReportInput): Promise<{ id: string; storedAt: string; receivedAt: string }> {
    await this.ensureLoaded();
    const { submission, serverUserAgent, clientReportedUserAgent, screenshotBytes } = input;
    const id = this.nextId();
    const ts = nowIso();

    await fs.mkdir(this.reportDir(id), { recursive: true });
    await fs.writeFile(this.screenshotFile(id), screenshotBytes);

    const report: StoredReport = {
      id,
      title: submission.title,
      description: submission.description ?? '',
      expected_behavior: submission.expected_behavior ?? '',
      report_type: submission.report_type ?? 'bug',
      severity: submission.severity ?? 'medium',
      status: 'open',
      tags: submission.tags ?? [],
      reporter: submission.reporter ?? {},
      context: submission.context ?? {},
      client_ts: submission.client_ts,
      protocol_version: submission.protocol_version,
      created_at: ts,
      updated_at: ts,
      archived_at: null,
      server_user_agent: serverUserAgent,
      client_reported_user_agent: clientReportedUserAgent,
      github_issue_url: null,
      github_issue_number: null,
      lifecycle: [
        {
          action: 'created',
          by: 'anonymous',
          at: ts,
          status: 'open',
          fix_commit: '',
          fix_description: ''
        }
      ]
    };

    await this.writeMeta(report);
    return { id, storedAt: `file://${this.reportDir(id)}/`, receivedAt: ts };
  }

  async getReport(id: string): Promise<BugReportDetail | null> {
    await this.ensureLoaded();
    const r = this.index.get(id);
    return r ? detailFrom(r) : null;
  }

  async listReports(
    filters: ListFilters,
    page: number,
    pageSize: number
  ): Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }> {
    await this.ensureLoaded();
    let rows = [...this.index.values()];

    if (!filters.include_archived) {
      rows = rows.filter((r) => !r.archived_at);
    }
    if (filters.status) rows = rows.filter((r) => r.status === filters.status);
    if (filters.severity) rows = rows.filter((r) => r.severity === filters.severity);
    if (filters.environment) {
      rows = rows.filter((r) => r.context?.environment === filters.environment);
    }

    // Stats are computed against the FILTERED set (before pagination), per
    // PROTOCOL.md § GET /reports.
    const stats: BugReportListStats = {
      open: rows.filter((r) => r.status === 'open').length,
      investigating: rows.filter((r) => r.status === 'investigating').length,
      fixed: rows.filter((r) => r.status === 'fixed').length,
      closed: rows.filter((r) => r.status === 'closed').length
    };

    rows.sort((a, b) => b.created_at.localeCompare(a.created_at));

    const total = rows.length;
    const items = rows.slice((page - 1) * pageSize, page * pageSize).map(summaryFrom);

    return { items, total, stats };
  }

  async getScreenshotPath(id: string): Promise<string | null> {
    await this.ensureLoaded();
    const r = this.index.get(id);
    const p = r ? join(this.dirFor(r), 'screenshot.png') : this.screenshotFile(id);
    return existsSync(p) ? p : null;
  }

  async getScreenshotBytes(id: string): Promise<Uint8Array | null> {
    const p = await this.getScreenshotPath(id);
    if (!p) return null;
    return new Uint8Array(await fs.readFile(p));
  }

  async updateStatus(
    id: string,
    newStatus: Status,
    by: string | null,
    fixCommit?: string,
    fixDescription?: string
  ): Promise<BugReportDetail> {
    await this.ensureLoaded();
    const r = this.index.get(id);
    if (!r) throw new Error(`Report not found: ${id}`);

    const entry: LifecycleEvent = {
      action: 'status_changed',
      // Schema default for `by` is "" — coalesce nulls/undefineds to empty
      // string so the wire shape matches the JSON Schema (audit A1).
      by: by ?? '',
      at: nowIso(),
      status: newStatus,
      fix_commit: fixCommit ?? '',
      fix_description: fixDescription ?? ''
    };

    const updated: StoredReport = {
      ...r,
      status: newStatus,
      updated_at: entry.at,
      lifecycle: [...r.lifecycle, entry]
    };
    await this.writeMeta(updated);
    return detailFrom(updated);
  }

  async deleteReport(id: string): Promise<void> {
    await this.ensureLoaded();
    const r = this.index.get(id);
    if (!r) throw new Error(`Report not found: ${id}`);
    const dir = this.dirFor(r);
    if (existsSync(dir)) await fs.rm(dir, { recursive: true, force: true });
    this.index.delete(id);
  }

  async archiveReport(id: string): Promise<void> {
    await this.ensureLoaded();
    const r = this.index.get(id);
    if (!r) throw new Error(`Report not found: ${id}`);
    if (r.archived_at) return; // already archived — idempotent

    const archiveDir = join(this.dir, 'archive', id);
    await fs.mkdir(archiveDir, { recursive: true });

    const srcShot = this.screenshotFile(id);
    if (existsSync(srcShot)) {
      await fs.copyFile(srcShot, join(archiveDir, 'screenshot.png'));
    }

    const ts = nowIso();
    const archived: StoredReport = {
      ...r,
      archived_at: ts,
      lifecycle: [
        ...r.lifecycle,
        { action: 'archived', by: 'system', at: ts, fix_commit: '', fix_description: '' }
      ]
    };
    await this.writeMeta(archived, archiveDir);

    // Remove the live copy. The report stays in the index with archived_at
    // set — dropping it made archived reports unreachable even through
    // include_archived=true.
    const liveDir = this.reportDir(id);
    if (existsSync(liveDir)) await fs.rm(liveDir, { recursive: true, force: true });
  }

  async bulkCloseFixed(): Promise<number> {
    await this.ensureLoaded();
    const fixed = [...this.index.values()].filter((r) => r.status === 'fixed' && !r.archived_at);
    for (const r of fixed) {
      await this.updateStatus(r.id, 'closed', 'system');
    }
    return fixed.length;
  }

  async bulkArchiveClosed(): Promise<number> {
    await this.ensureLoaded();
    const closed = [...this.index.values()].filter((r) => r.status === 'closed' && !r.archived_at);
    for (const r of closed) {
      await this.archiveReport(r.id);
    }
    return closed.length;
  }

  async setGitHubIssue(id: string, issueUrl: string, issueNumber: number): Promise<void> {
    await this.ensureLoaded();
    const r = this.index.get(id);
    if (!r) return;
    const updated: StoredReport = {
      ...r,
      github_issue_url: issueUrl,
      github_issue_number: issueNumber
    };
    await this.writeMeta(updated);
  }
}
