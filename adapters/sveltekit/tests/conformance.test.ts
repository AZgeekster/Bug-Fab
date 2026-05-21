// Local conformance probe — exercises the handler factories against the
// 12-point Adapter Authorship Checklist (repo/docs/ADAPTERS_REGISTRY.md).
//
// This is NOT the official conformance suite — that is `pytest --bug-fab-conformance`
// and runs against a live HTTP server. This file documents the same invariants
// in TS-land so a contributor without Python can sanity-check changes locally.
// The official suite is the source of truth and is run in CI by the consumer.

import { describe, it, expect, beforeEach } from 'vitest';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FileStorage } from '../src/server/storage/FileStorage.js';
import { createIntakeHandler } from '../src/server/intake.js';
import { createListHandler } from '../src/server/viewer/list.js';
import { createStatusHandler } from '../src/server/viewer/status.js';
import { createDetailHandler } from '../src/server/viewer/detail.js';
import { createViewerIndexHandler } from '../src/server/viewer/index.js';

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function multipartEvent(metadata: object, png: Uint8Array = PNG, ua = 'TestUA'): {
  request: Request;
  url: URL;
  params: Record<string, string>;
} {
  const fd = new FormData();
  fd.append('metadata', JSON.stringify(metadata));
  fd.append('screenshot', new Blob([png], { type: 'image/png' }), 'shot.png');
  const req = new Request('http://localhost/api/bug-reports', {
    method: 'POST',
    body: fd,
    headers: { 'user-agent': ua }
  });
  return { request: req, url: new URL(req.url), params: {} };
}

const valid = {
  protocol_version: '0.1',
  title: 'Conformance check',
  client_ts: '2026-04-30T12:00:00Z'
};

describe('Adapter Authorship Checklist conformance', () => {
  let storage: FileStorage;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), 'bug-fab-conformance-'));
    storage = new FileStorage({ storageDir: dir });
  });

  it('1. wire-protocol contract: success response includes id, received_at, stored_at, github_issue_url', async () => {
    const resp = await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const body = await resp.json();
    expect(body).toHaveProperty('id');
    expect(body).toHaveProperty('received_at');
    expect(body).toHaveProperty('stored_at');
    expect(body).toHaveProperty('github_issue_url');
  });

  it('4. validation: severity coercion is rejected, not silently rewritten', async () => {
    const resp = await createIntakeHandler({ storage })(
      multipartEvent({ ...valid, severity: 'urgent' }) as never
    );
    expect(resp.status).toBe(422);
    const body = await resp.json();
    expect(body.error).toBe('schema_error');
  });

  it('4. validation: protocol_version != "0.1" rejected as 400 unsupported_protocol_version', async () => {
    const resp = await createIntakeHandler({ storage })(
      multipartEvent({ ...valid, protocol_version: '0.2' }) as never
    );
    expect(resp.status).toBe(400);
    const body = await resp.json();
    expect(body.error).toBe('unsupported_protocol_version');
  });

  it('4. validation: client_ts is required', async () => {
    const { client_ts: _, ...rest } = valid;
    const resp = await createIntakeHandler({ storage })(multipartEvent(rest as object) as never);
    expect(resp.status).toBe(422);
  });

  it('4. validation: PNG magic bytes verified', async () => {
    const resp = await createIntakeHandler({ storage })(
      multipartEvent(valid, new Uint8Array([0xff, 0xd8, 0xff, 0xe0])) as never
    );
    expect(resp.status).toBe(415);
  });

  it('5. error envelope: every non-2xx body has {error, detail}', async () => {
    const resp = await createIntakeHandler({ storage })(multipartEvent({}) as never);
    expect(resp.status).toBeGreaterThanOrEqual(400);
    const body = await resp.json();
    expect(body).toHaveProperty('error');
    expect(body).toHaveProperty('detail');
  });

  it('9. lifecycle: created entry uses "anonymous" when no actor resolver', async () => {
    const resp = await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const { id } = await resp.json();
    const detail = await storage.getReport(id);
    expect(detail?.lifecycle[0]).toMatchObject({ action: 'created', by: 'anonymous' });
  });

  it('9. lifecycle: status_changed appends a new entry without mutating prior ones', async () => {
    const resp = await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const { id } = await resp.json();
    const before = await storage.getReport(id);

    await createStatusHandler({ storage })({
      request: new Request(`http://localhost/admin/reports/${id}/status`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ status: 'investigating' })
      }),
      url: new URL(`http://localhost/admin/reports/${id}/status`),
      params: { id }
    } as never);

    const after = await storage.getReport(id);
    expect(after?.lifecycle).toHaveLength((before?.lifecycle.length ?? 0) + 1);
    // Prior entry is identical.
    expect(after?.lifecycle[0]).toEqual(before?.lifecycle[0]);
  });

  it('11. snake_case: list response keys are snake_case across the wire', async () => {
    await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const resp = await createListHandler({ storage })({
      request: new Request('http://localhost/admin/reports'),
      url: new URL('http://localhost/admin/reports'),
      params: {}
    } as never);
    const body = await resp.json();
    expect(body).toHaveProperty('page_size');
    expect(body).not.toHaveProperty('pageSize');
    expect(body.items[0]).toHaveProperty('has_screenshot');
    expect(body.items[0]).toHaveProperty('report_type');
    expect(body.items[0]).not.toHaveProperty('hasScreenshot');
  });

  it('User-Agent trust boundary: server captures from header, not from client body', async () => {
    const resp = await createIntakeHandler({ storage })(
      multipartEvent({ ...valid, context: { user_agent: 'spoofed' } }, PNG, 'real-server-ua') as never
    );
    const { id } = await resp.json();
    const detail = await storage.getReport(id);
    expect(detail?.server_user_agent).toBe('real-server-ua');
    expect(detail?.client_reported_user_agent).toBe('spoofed');
  });

  it('deprecated-values rule: list path passes unknown status filter through (does not 422)', async () => {
    const resp = await createListHandler({ storage })({
      request: new Request('http://localhost/admin/reports?status=somehistorical'),
      url: new URL('http://localhost/admin/reports?status=somehistorical'),
      params: {}
    } as never);
    expect(resp.status).toBe(200);
  });

  // --- Audit 2026-05-01 § A1 regression: schema-default fields MUST emit
  // empty strings, never `undefined` (which JSON.stringify drops). The wire
  // protocol declares `module`, `expected_behavior`, `client_reported_user_agent`,
  // `environment`, `client_ts`, plus LifecycleEvent's `by`/`fix_commit`/
  // `fix_description` as `string` with `default: ""`. Adapters that emit
  // `undefined` produce JSON missing those keys, which fails conformance.
  it('A1 regression: detail response emits "" for schema-default string fields, never undefined', async () => {
    // Submit a minimal report — no module, no expected_behavior, no
    // context.environment, no client UA. Storage must still emit "" for
    // each on the detail wire shape.
    const resp = await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const { id } = await resp.json();

    const detailResp = await createDetailHandler({ storage })({
      request: new Request(`http://localhost/admin/reports/${id}`),
      url: new URL(`http://localhost/admin/reports/${id}`),
      params: { id }
    } as never);
    const detail = await detailResp.json();

    // Every schema-default-"" field MUST be a string (never undefined).
    for (const field of [
      'module',
      'expected_behavior',
      'client_reported_user_agent',
      'environment',
      'client_ts',
      'description',
      'updated_at',
      'server_user_agent'
    ]) {
      expect(typeof detail[field]).toBe('string');
    }

    // Lifecycle entries must have string-typed by/fix_commit/fix_description.
    expect(detail.lifecycle.length).toBeGreaterThan(0);
    for (const event of detail.lifecycle) {
      expect(typeof event.by).toBe('string');
      expect(typeof event.fix_commit).toBe('string');
      expect(typeof event.fix_description).toBe('string');
    }
  });

  it('A1 regression: list summary emits "" for module rather than omitting it', async () => {
    await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const resp = await createListHandler({ storage })({
      request: new Request('http://localhost/admin/reports'),
      url: new URL('http://localhost/admin/reports'),
      params: {}
    } as never);
    const body = await resp.json();
    expect(body.items.length).toBeGreaterThan(0);
    expect(typeof body.items[0].module).toBe('string');
  });

  // --- Audit 2026-05-01 § A3 regression: viewer mount root (`GET ""`) MUST
  // serve text/html so adapter-checklist item 6 (mount-prefix invariant) is
  // honored. Bare-JSON-only viewer mounts produce a 404 / JSON error at the
  // viewer root, which breaks the human-readable index promise.
  it('A3 regression: viewer index serves text/html at the mount root', async () => {
    const resp = await createViewerIndexHandler({ storage })({
      request: new Request('http://localhost/admin/reports'),
      url: new URL('http://localhost/admin/reports'),
      params: {}
    } as never);
    expect(resp.status).toBe(200);
    const ct = resp.headers.get('content-type') ?? '';
    expect(ct.toLowerCase()).toContain('text/html');
    const body = await resp.text();
    expect(body).toContain('<title>Bug Reports - Bug-Fab</title>');
  });

  it('A3 regression: viewer index renders submitted reports as table rows', async () => {
    await createIntakeHandler({ storage })(multipartEvent(valid) as never);
    const resp = await createViewerIndexHandler({ storage })({
      request: new Request('http://localhost/admin/reports'),
      url: new URL('http://localhost/admin/reports'),
      params: {}
    } as never);
    const body = await resp.text();
    // Title from the submitted report should appear in a table cell.
    expect(body).toContain('Conformance check');
    // Stat cards should be present.
    expect(body).toContain('bug-fab-stats');
  });
});
