// Viewer handler tests — list, detail, status, delete, screenshot, bulk.

import { describe, it, expect, beforeEach } from 'vitest';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FileStorage } from '../src/server/storage/FileStorage.js';
import { createListHandler } from '../src/server/viewer/list.js';
import { createDetailHandler } from '../src/server/viewer/detail.js';
import { createStatusHandler } from '../src/server/viewer/status.js';
import { createDeleteHandler } from '../src/server/viewer/delete.js';
import { createScreenshotHandler } from '../src/server/viewer/screenshot.js';
import { createBulkCloseHandler } from '../src/server/viewer/bulk-close.js';
import { createBulkArchiveHandler } from '../src/server/viewer/bulk-archive.js';

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function makeEvent(url: string, opts: { params?: Record<string, string>; method?: string; body?: unknown } = {}): {
  request: Request;
  url: URL;
  params: Record<string, string>;
} {
  const init: RequestInit = {
    method: opts.method ?? 'GET',
    headers: { 'content-type': 'application/json' }
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  return {
    request: new Request(url, init),
    url: new URL(url),
    params: opts.params ?? {}
  };
}

async function seed(storage: FileStorage, n: number): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < n; i++) {
    const { id } = await storage.saveReport({
      submission: {
        protocol_version: '0.1',
        title: `Bug ${i}`,
        client_ts: '2026-04-30T12:00:00Z',
        severity: i % 2 === 0 ? 'high' : 'low',
        context: { environment: i < 2 ? 'prod' : 'dev' }
      },
      serverUserAgent: 'TestUA',
      screenshotBytes: PNG
    });
    ids.push(id);
  }
  return ids;
}

describe('viewer handlers', () => {
  let dir: string;
  let storage: FileStorage;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'bug-fab-sveltekit-viewer-'));
    storage = new FileStorage({ storageDir: dir });
  });

  it('list returns items, total, page, page_size, stats', async () => {
    await seed(storage, 3);
    const handler = createListHandler({ storage });
    const resp = await handler(makeEvent('http://localhost/admin/reports?page=1&page_size=2') as never);
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.total).toBe(3);
    expect(body.page).toBe(1);
    expect(body.page_size).toBe(2);
    expect(body.items).toHaveLength(2);
    expect(body.stats).toMatchObject({ open: 3, investigating: 0, fixed: 0, closed: 0 });
  });

  it('list filters by environment', async () => {
    await seed(storage, 4);
    const handler = createListHandler({ storage });
    const resp = await handler(makeEvent('http://localhost/admin/reports?environment=prod') as never);
    const body = await resp.json();
    expect(body.total).toBe(2);
  });

  it('list caps page_size at MAX_PAGE_SIZE', async () => {
    await seed(storage, 1);
    const handler = createListHandler({ storage });
    const resp = await handler(makeEvent('http://localhost/admin/reports?page_size=9999') as never);
    const body = await resp.json();
    expect(body.page_size).toBe(200);
  });

  it('detail returns 404 for missing id', async () => {
    const handler = createDetailHandler({ storage });
    const resp = await handler(makeEvent('http://localhost/admin/reports/bug-999', { params: { id: 'bug-999' } }) as never);
    expect(resp.status).toBe(404);
    const body = await resp.json();
    expect(body.error).toBe('not_found');
  });

  it('detail returns full BugReportDetail on hit', async () => {
    const [id] = await seed(storage, 1);
    const handler = createDetailHandler({ storage });
    const resp = await handler(makeEvent(`http://localhost/admin/reports/${id}`, { params: { id } }) as never);
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.id).toBe(id);
    expect(body.protocol_version).toBe('0.1');
    expect(body.lifecycle).toHaveLength(1);
    expect(body.lifecycle[0].action).toBe('created');
  });

  it('status update rejects invalid status with 422', async () => {
    const [id] = await seed(storage, 1);
    const handler = createStatusHandler({ storage });
    const resp = await handler(
      makeEvent(`http://localhost/admin/reports/${id}/status`, {
        params: { id },
        method: 'PUT',
        body: { status: 'maybe-fixed' }
      }) as never
    );
    expect(resp.status).toBe(422);
    const body = await resp.json();
    expect(body.error).toBe('schema_error');
  });

  it('status update transitions and appends lifecycle', async () => {
    const [id] = await seed(storage, 1);
    const handler = createStatusHandler({ storage });
    const resp = await handler(
      makeEvent(`http://localhost/admin/reports/${id}/status`, {
        params: { id },
        method: 'PUT',
        body: { status: 'fixed', fix_commit: 'abc123', fix_description: 'Re-attached event listener.' }
      }) as never
    );
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe('fixed');
    expect(body.lifecycle).toHaveLength(2);
    expect(body.lifecycle[1]).toMatchObject({
      action: 'status_changed',
      status: 'fixed',
      fix_commit: 'abc123'
    });
  });

  it('status update honors resolveActor for `by`', async () => {
    const [id] = await seed(storage, 1);
    const handler = createStatusHandler({
      storage,
      resolveActor: () => 'admin@example.com'
    });
    const resp = await handler(
      makeEvent(`http://localhost/admin/reports/${id}/status`, {
        params: { id },
        method: 'PUT',
        body: { status: 'investigating' }
      }) as never
    );
    const body = await resp.json();
    expect(body.lifecycle.at(-1).by).toBe('admin@example.com');
  });

  it('delete returns 204 on success and 404 on missing', async () => {
    const [id] = await seed(storage, 1);
    const handler = createDeleteHandler({ storage });
    let resp = await handler(
      makeEvent(`http://localhost/admin/reports/${id}`, { params: { id }, method: 'DELETE' }) as never
    );
    expect(resp.status).toBe(204);
    resp = await handler(
      makeEvent(`http://localhost/admin/reports/${id}`, { params: { id }, method: 'DELETE' }) as never
    );
    expect(resp.status).toBe(404);
  });

  it('screenshot returns image/png bytes', async () => {
    const [id] = await seed(storage, 1);
    const handler = createScreenshotHandler({ storage });
    const resp = await handler(
      makeEvent(`http://localhost/admin/reports/${id}/screenshot`, { params: { id } }) as never
    );
    expect(resp.status).toBe(200);
    expect(resp.headers.get('content-type')).toBe('image/png');
    const ab = new Uint8Array(await resp.arrayBuffer());
    expect(Array.from(ab.subarray(0, 8))).toEqual(Array.from(PNG));
  });

  it('bulk-close-fixed transitions only fixed reports', async () => {
    const ids = await seed(storage, 3);
    const statusHandler = createStatusHandler({ storage });
    // Mark first two as fixed.
    for (const id of ids.slice(0, 2)) {
      await statusHandler(
        makeEvent(`http://localhost/admin/reports/${id}/status`, {
          params: { id },
          method: 'PUT',
          body: { status: 'fixed' }
        }) as never
      );
    }
    const handler = createBulkCloseHandler({ storage });
    const resp = await handler(
      makeEvent('http://localhost/admin/bulk-close-fixed', { method: 'POST' }) as never
    );
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.closed).toBe(2);
  });

  it('bulk-archive-closed archives closed reports', async () => {
    const ids = await seed(storage, 2);
    const statusHandler = createStatusHandler({ storage });
    for (const id of ids) {
      await statusHandler(
        makeEvent(`http://localhost/admin/reports/${id}/status`, {
          params: { id },
          method: 'PUT',
          body: { status: 'closed' }
        }) as never
      );
    }
    const handler = createBulkArchiveHandler({ storage });
    const resp = await handler(
      makeEvent('http://localhost/admin/bulk-archive-closed', { method: 'POST' }) as never
    );
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.archived).toBe(2);
  });

  // tmpdir prefix avoids needing explicit teardown
  it.skip('cleanup', () => {});
});
