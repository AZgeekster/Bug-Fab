// Intake tests — POST /bug-reports
//
// Hono apps expose `app.fetch(req)` so we drive them with the Web
// `Request` constructor directly — no supertest, no test server.

import { describe, it, expect, beforeEach } from 'vitest'
import { createBugFabApp, MemoryStorage } from '../src/index.js'
import type { Hono } from 'hono'

// Minimal valid PNG: 8-byte magic + IHDR + IEND. Real bug-reports use
// html2canvas's full PNG; for validation tests we only need the magic
// bytes plus a tiny tail.
const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, // PNG magic
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52, // IHDR length + tag
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, // 1x1
  0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4, 0x89,
  0x00, 0x00, 0x00, 0x0a, 0x49, 0x44, 0x41, 0x54, // IDAT
  0x78, 0x9c, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01,
  0x0d, 0x0a, 0x2d, 0xb4,
  0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, // IEND
  0xae, 0x42, 0x60, 0x82,
])

const JPEG_BYTES = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10])

function buildFormData(metadata: object | string, screenshot: Uint8Array | null): FormData {
  const fd = new FormData()
  if (typeof metadata === 'string') {
    fd.append('metadata', metadata)
  } else {
    fd.append('metadata', JSON.stringify(metadata))
  }
  if (screenshot) {
    fd.append('screenshot', new Blob([screenshot], { type: 'image/png' }), 'screenshot.png')
  }
  return fd
}

const VALID_METADATA = {
  protocol_version: '0.1',
  title: 'Save button is unresponsive',
  client_ts: '2026-04-27T15:29:58-07:00',
  description: 'Click does nothing',
  severity: 'high',
  context: { url: 'https://example.com/cart', user_agent: 'Mozilla/5.0' },
}

describe('POST /api/bug-reports — intake', () => {
  let app: Hono
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    app = createBugFabApp({ storage })
  })

  it('returns 201 with minimal envelope on success', async () => {
    const fd = buildFormData(VALID_METADATA, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(201)
    const body = await res.json()
    expect(Object.keys(body).sort()).toEqual(
      ['github_issue_url', 'id', 'received_at', 'stored_at'].sort(),
    )
    expect(body.id).toMatch(/^bug-\d{3,}$/)
    expect(body.github_issue_url).toBeNull()
  })

  it('does NOT echo user-submitted free text in the 201 envelope (privacy)', async () => {
    const fd = buildFormData(VALID_METADATA, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    const body = await res.json()
    expect(body.title).toBeUndefined()
    expect(body.description).toBeUndefined()
    expect(body.severity).toBeUndefined()
  })

  it('captures server User-Agent independently of client-supplied value', async () => {
    const fd = buildFormData(VALID_METADATA, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', {
        method: 'POST',
        body: fd,
        headers: { 'user-agent': 'TestBot/1.0' },
      }),
    )
    const { id } = await res.json()
    const stored = await storage.getReport(id)
    expect(stored?.server_user_agent).toBe('TestBot/1.0')
    expect(stored?.client_reported_user_agent).toBe('Mozilla/5.0')
  })

  it('rejects missing metadata part with 400 validation_error', async () => {
    const fd = new FormData()
    fd.append('screenshot', new Blob([PNG_BYTES], { type: 'image/png' }), 'screenshot.png')
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('validation_error')
  })

  it('rejects missing screenshot part with 400 validation_error', async () => {
    const fd = new FormData()
    fd.append('metadata', JSON.stringify(VALID_METADATA))
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('validation_error')
  })

  it('rejects non-PNG screenshot with 415 unsupported_media_type', async () => {
    const fd = buildFormData(VALID_METADATA, JPEG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(415)
    const body = await res.json()
    expect(body.error).toBe('unsupported_media_type')
  })

  it('rejects unknown protocol_version with 400 unsupported_protocol_version', async () => {
    const fd = buildFormData({ ...VALID_METADATA, protocol_version: '9.9' }, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('unsupported_protocol_version')
  })

  it('rejects missing protocol_version with 400 unsupported_protocol_version', async () => {
    const { protocol_version: _ignore, ...withoutVersion } = VALID_METADATA
    const fd = buildFormData(withoutVersion, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('unsupported_protocol_version')
  })

  it('rejects invalid severity with 422 schema_error (no silent coercion)', async () => {
    const fd = buildFormData({ ...VALID_METADATA, severity: 'urgent' }, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(422)
    const body = await res.json()
    expect(body.error).toBe('schema_error')
    expect(String(body.detail)).toContain('severity')
  })

  it('rejects invalid report_type with 422 schema_error', async () => {
    const fd = buildFormData({ ...VALID_METADATA, report_type: 'enhancement' }, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(422)
    const body = await res.json()
    expect(body.error).toBe('schema_error')
  })

  it('rejects missing title with 422 schema_error', async () => {
    const { title: _t, ...noTitle } = VALID_METADATA
    const fd = buildFormData(noTitle, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(422)
  })

  it('rejects missing client_ts with 422 schema_error', async () => {
    const { client_ts: _c, ...noTs } = VALID_METADATA
    const fd = buildFormData(noTs, PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(422)
  })

  it('rejects reporter sub-fields longer than 256 chars', async () => {
    const longEmail = 'a'.repeat(257)
    const fd = buildFormData(
      { ...VALID_METADATA, reporter: { email: longEmail } },
      PNG_BYTES,
    )
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(422)
    const body = await res.json()
    expect(String(body.detail)).toContain('reporter.email')
  })

  it('rejects malformed metadata JSON with 400 validation_error', async () => {
    const fd = buildFormData('{not json', PNG_BYTES)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('validation_error')
  })

  it('returns 413 with limit_bytes when screenshot exceeds 10 MiB', async () => {
    const oversized = new Uint8Array(10 * 1024 * 1024 + 1)
    oversized.set(PNG_BYTES, 0)
    const fd = buildFormData(VALID_METADATA, oversized)
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
    )
    expect(res.status).toBe(413)
    const body = await res.json()
    expect(body.error).toBe('payload_too_large')
    expect(body.limit_bytes).toBe(10 * 1024 * 1024)
  })

  it('rejects non-multipart Content-Type with 415 unsupported_media_type', async () => {
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', {
        method: 'POST',
        body: JSON.stringify(VALID_METADATA),
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(415)
    const body = await res.json()
    expect(body.error).toBe('unsupported_media_type')
  })
})
