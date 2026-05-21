// Intake handler tests.
//
// These tests construct the SvelteKit RequestEvent shape directly and invoke
// the handler. We don't spin up a real SvelteKit server — that would require
// vite + the kit dev runtime, which the package doesn't depend on.

import { describe, it, expect, beforeEach } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createIntakeHandler } from '../src/server/intake.js';
import { FileStorage } from '../src/server/storage/FileStorage.js';

// Minimal PNG: 8-byte magic + IHDR (size 13) header + CRC.
const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
  0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,
  0x89
]);

function buildEvent(form: FormData, headers: Record<string, string> = {}): {
  request: Request;
  url: URL;
  params: Record<string, string>;
  locals?: unknown;
} {
  const req = new Request('http://localhost/api/bug-reports', {
    method: 'POST',
    body: form,
    headers
  });
  return { request: req, url: new URL(req.url), params: {} };
}

function metaForm(metadata: object, png: Uint8Array = PNG_BYTES): FormData {
  const fd = new FormData();
  fd.append('metadata', JSON.stringify(metadata));
  fd.append('screenshot', new Blob([png], { type: 'image/png' }), 'screenshot.png');
  return fd;
}

const validMeta = {
  protocol_version: '0.1',
  title: 'Save button broken',
  client_ts: '2026-04-30T12:00:00Z',
  severity: 'high'
};

describe('createIntakeHandler', () => {
  let dir: string;
  let storage: FileStorage;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'bug-fab-sveltekit-'));
    storage = new FileStorage({ storageDir: dir });
  });

  it('accepts a valid submission and returns 201 with id + received_at + stored_at', async () => {
    const handler = createIntakeHandler({ storage });
    const event = buildEvent(metaForm(validMeta), { 'user-agent': 'TestUA/1.0' });
    // SvelteKit attaches getClientAddress; we don't use it here.
    const resp = await handler(event as never);
    expect(resp.status).toBe(201);
    const body = await resp.json();
    expect(body.id).toMatch(/^bug-\d{3,}$/);
    expect(typeof body.received_at).toBe('string');
    expect(typeof body.stored_at).toBe('string');
    expect(body.github_issue_url).toBeNull();
  });

  it('rejects missing protocol_version with 400 unsupported_protocol_version', async () => {
    const handler = createIntakeHandler({ storage });
    const { protocol_version, ...rest } = validMeta;
    void protocol_version;
    const resp = await handler(buildEvent(metaForm(rest as object)) as never);
    expect(resp.status).toBe(400);
    const body = await resp.json();
    expect(body.error).toBe('unsupported_protocol_version');
  });

  it('rejects severity="urgent" with 422 schema_error (no silent coercion)', async () => {
    const handler = createIntakeHandler({ storage });
    const resp = await handler(buildEvent(metaForm({ ...validMeta, severity: 'urgent' })) as never);
    expect(resp.status).toBe(422);
    const body = await resp.json();
    expect(body.error).toBe('schema_error');
  });

  it('rejects non-PNG screenshot with 415 unsupported_media_type', async () => {
    const handler = createIntakeHandler({ storage });
    const fd = new FormData();
    fd.append('metadata', JSON.stringify(validMeta));
    fd.append(
      'screenshot',
      new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: 'image/jpeg' }),
      'shot.jpg'
    );
    const resp = await handler(buildEvent(fd) as never);
    expect(resp.status).toBe(415);
    const body = await resp.json();
    expect(body.error).toBe('unsupported_media_type');
  });

  it('rejects missing screenshot with 400 validation_error', async () => {
    const handler = createIntakeHandler({ storage });
    const fd = new FormData();
    fd.append('metadata', JSON.stringify(validMeta));
    const resp = await handler(buildEvent(fd) as never);
    expect(resp.status).toBe(400);
    const body = await resp.json();
    expect(body.error).toBe('validation_error');
  });

  it('rejects screenshot exceeding maxScreenshotBytes with 413', async () => {
    const handler = createIntakeHandler({ storage, maxScreenshotBytes: 100 });
    const big = new Uint8Array(200);
    big.set(PNG_BYTES); // start with valid PNG magic
    const fd = new FormData();
    fd.append('metadata', JSON.stringify(validMeta));
    fd.append('screenshot', new Blob([big], { type: 'image/png' }), 'shot.png');
    const resp = await handler(buildEvent(fd) as never);
    expect(resp.status).toBe(413);
    const body = await resp.json();
    expect(body.error).toBe('payload_too_large');
    expect(body.limit_bytes).toBe(100);
  });

  it('captures server-side User-Agent independently from client_reported_user_agent', async () => {
    const handler = createIntakeHandler({ storage });
    const event = buildEvent(
      metaForm({
        ...validMeta,
        context: { user_agent: 'ClientSpoofedUA' }
      }),
      { 'user-agent': 'RealServerUA/2.0' }
    );
    const resp = await handler(event as never);
    expect(resp.status).toBe(201);
    const body = await resp.json();
    const detail = await storage.getReport(body.id);
    expect(detail?.server_user_agent).toBe('RealServerUA/2.0');
    expect(detail?.client_reported_user_agent).toBe('ClientSpoofedUA');
  });

  // Cleanup is handled per-process; tmp dirs are short-lived in CI.
  it('cleanup probe (no assertion)', () => {
    rmSync(dir, { recursive: true, force: true });
    expect(true).toBe(true);
  });
});
