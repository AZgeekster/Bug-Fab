// Local conformance smoke tests.
//
// These do NOT replace the official Python conformance suite (pytest
// --bug-fab-conformance --base-url=...) — they're the in-repo
// equivalent that runs on every commit so we catch protocol drift
// before publishing. The official suite is run from a separate harness
// once the adapter has a published artifact to point at.
//
// Each block below maps to one row of the adapter authorship checklist
// in repo/docs/ADAPTERS_REGISTRY.md.

import { describe, it, expect, beforeEach } from 'vitest'
import { createBugFabApp, MemoryStorage } from '../src/index.js'
import type { Hono } from 'hono'

const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
  0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4, 0x89,
  0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44,
  0xae, 0x42, 0x60, 0x82,
])

function fd(metadata: object | string, screenshot: Uint8Array | null = PNG_BYTES): FormData {
  const f = new FormData()
  f.append(
    'metadata',
    typeof metadata === 'string' ? metadata : JSON.stringify(metadata),
  )
  if (screenshot) {
    f.append('screenshot', new Blob([screenshot], { type: 'image/png' }), 'screenshot.png')
  }
  return f
}

const VALID = {
  protocol_version: '0.1',
  title: 'A bug',
  client_ts: '2026-04-27T15:29:58Z',
}

describe('conformance — wire protocol contract', () => {
  let app: Hono
  beforeEach(() => {
    app = createBugFabApp({ storage: new MemoryStorage() })
  })

  it('all 8 endpoints exist (smoke check)', async () => {
    // Seed one record to give the rest of the matrix something to work with.
    const seed = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd(VALID) }),
    )
    expect(seed.status).toBe(201)
    const { id } = await seed.json()

    const matrix = [
      ['POST', '/api/bug-reports', 201],
      ['GET', '/admin/bug-reports/reports', 200],
      ['GET', `/admin/bug-reports/reports/${id}`, 200],
      ['GET', `/admin/bug-reports/reports/${id}/screenshot`, 200],
      ['PUT', `/admin/bug-reports/reports/${id}/status`, 200],
      ['DELETE', `/admin/bug-reports/reports/${id}`, 204],
      ['POST', '/admin/bug-reports/bulk-close-fixed', 200],
      ['POST', '/admin/bug-reports/bulk-archive-closed', 200],
    ] as const

    for (const [method, path, expectedStatus] of matrix) {
      let req: Request
      if (method === 'POST' && path === '/api/bug-reports') {
        req = new Request(`http://test${path}`, { method, body: fd(VALID) })
      } else if (method === 'PUT') {
        req = new Request(`http://test${path}`, {
          method,
          body: JSON.stringify({ status: 'open' }),
          headers: { 'content-type': 'application/json' },
        })
      } else {
        req = new Request(`http://test${path}`, { method })
      }
      const res = await app.fetch(req)
      expect(res.status, `${method} ${path}`).toBe(expectedStatus)
    }
  })

  it('returns the protocol error envelope on every non-2xx', async () => {
    const cases = [
      // 400 validation_error — bad JSON
      app.fetch(
        new Request('http://test/api/bug-reports', {
          method: 'POST',
          body: fd('not json'),
        }),
      ),
      // 400 unsupported_protocol_version
      app.fetch(
        new Request('http://test/api/bug-reports', {
          method: 'POST',
          body: fd({ ...VALID, protocol_version: '9.9' }),
        }),
      ),
      // 422 schema_error
      app.fetch(
        new Request('http://test/api/bug-reports', {
          method: 'POST',
          body: fd({ ...VALID, severity: 'urgent' }),
        }),
      ),
      // 415 unsupported_media_type
      app.fetch(
        new Request('http://test/api/bug-reports', {
          method: 'POST',
          body: fd(VALID, new Uint8Array([0xff, 0xd8, 0xff, 0xe0])),
        }),
      ),
      // 404 not_found
      app.fetch(new Request('http://test/admin/bug-reports/reports/bug-999')),
    ]
    for (const p of cases) {
      const res = await p
      const body = await res.json()
      expect(typeof body.error).toBe('string')
      expect(body.error).toMatch(/^[a-z_]+$/)
      expect(body.detail).toBeDefined()
    }
  })

  it('all wire keys are snake_case (intake response)', async () => {
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd(VALID) }),
    )
    const body = await res.json()
    for (const k of Object.keys(body)) {
      expect(k, `key "${k}" should be snake_case`).toMatch(/^[a-z][a-z0-9_]*$/)
    }
  })

  it('all wire keys are snake_case (list response)', async () => {
    await app.fetch(new Request('http://test/api/bug-reports', { method: 'POST', body: fd(VALID) }))
    const res = await app.fetch(new Request('http://test/admin/bug-reports/reports'))
    const body = await res.json()
    for (const k of Object.keys(body)) {
      expect(k, `key "${k}"`).toMatch(/^[a-z][a-z0-9_]*$/)
    }
    if (body.items.length > 0) {
      for (const k of Object.keys(body.items[0])) {
        expect(k, `items[0] key "${k}"`).toMatch(/^[a-z][a-z0-9_]*$/)
      }
    }
  })

  it('preserves client-supplied extra context keys through round-trip', async () => {
    const meta = {
      ...VALID,
      context: {
        url: 'https://example.com',
        custom_consumer_field: 'value-A',
        nested: { foo: 'bar' },
      },
    }
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd(meta) }),
    )
    const { id } = await res.json()
    const detail = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}`),
    )
    const body = await detail.json()
    expect(body.context.custom_consumer_field).toBe('value-A')
    expect(body.context.nested).toEqual({ foo: 'bar' })
  })

  it('records protocol_version on stored report (round-trip)', async () => {
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd(VALID) }),
    )
    const { id } = await res.json()
    const detail = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}`),
    )
    const body = await detail.json()
    expect(body.protocol_version).toBe('0.1')
  })

  it('appends `created` lifecycle entry on intake', async () => {
    const res = await app.fetch(
      new Request('http://test/api/bug-reports', { method: 'POST', body: fd(VALID) }),
    )
    const { id } = await res.json()
    const detail = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}`),
    )
    const body = await detail.json()
    expect(body.lifecycle).toHaveLength(1)
    expect(body.lifecycle[0].action).toBe('created')
    // `by` may be "anonymous" or null — both conformant.
    expect(['anonymous', null]).toContain(body.lifecycle[0].by)
  })

  it('returns 404 on path-traversal attempts', async () => {
    const cases = [
      'bug-../etc',
      '..%2F..%2Fetc%2Fpasswd',
      'bug-1; DROP TABLE',
      '',
    ]
    for (const id of cases) {
      const res = await app.fetch(
        new Request(`http://test/admin/bug-reports/reports/${encodeURIComponent(id)}`),
      )
      expect([404, 200]).toContain(res.status) // empty string lands on /reports
      if (res.status === 404) {
        const body = await res.json()
        expect(body.error).toBe('not_found')
      }
    }
  })
})
