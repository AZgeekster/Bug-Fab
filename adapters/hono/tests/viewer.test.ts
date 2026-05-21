// Viewer tests — list, detail, screenshot, status update, delete, bulk ops.

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

async function seedReport(
  app: Hono,
  overrides: Partial<{ severity: string; title: string }> = {},
): Promise<string> {
  const fd = new FormData()
  fd.append(
    'metadata',
    JSON.stringify({
      protocol_version: '0.1',
      title: overrides.title ?? 'Bug',
      client_ts: '2026-04-27T15:29:58Z',
      severity: overrides.severity ?? 'medium',
    }),
  )
  fd.append('screenshot', new Blob([PNG_BYTES], { type: 'image/png' }), 'screenshot.png')
  const res = await app.fetch(
    new Request('http://test/api/bug-reports', { method: 'POST', body: fd }),
  )
  const { id } = await res.json()
  return id
}

describe('viewer routes', () => {
  let app: Hono
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    app = createBugFabApp({ storage })
  })

  it('GET /admin/bug-reports/reports returns paginated list with stats', async () => {
    await seedReport(app, { severity: 'low' })
    await seedReport(app, { severity: 'high' })

    const res = await app.fetch(new Request('http://test/admin/bug-reports/reports'))
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.total).toBe(2)
    expect(body.page).toBe(1)
    expect(body.page_size).toBe(20)
    expect(body.items).toHaveLength(2)
    expect(body.stats).toEqual({ open: 2, investigating: 0, fixed: 0, closed: 0 })
  })

  it('rejects invalid status filter with 422', async () => {
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/reports?status=resolved'),
    )
    expect(res.status).toBe(422)
    const body = await res.json()
    expect(body.error).toBe('schema_error')
  })

  it('rejects invalid severity filter with 422', async () => {
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/reports?severity=urgent'),
    )
    expect(res.status).toBe(422)
  })

  it('GET /admin/bug-reports/reports/:id returns full detail', async () => {
    const id = await seedReport(app)
    const res = await app.fetch(new Request(`http://test/admin/bug-reports/reports/${id}`))
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.id).toBe(id)
    expect(body.protocol_version).toBe('0.1')
    expect(Array.isArray(body.lifecycle)).toBe(true)
    expect(body.lifecycle[0]?.action).toBe('created')
  })

  it('returns 404 for missing report', async () => {
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/reports/bug-999'),
    )
    expect(res.status).toBe(404)
    const body = await res.json()
    expect(body.error).toBe('not_found')
  })

  it('rejects path-traversal-style report IDs with 404', async () => {
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/reports/..%2Fetc%2Fpasswd'),
    )
    expect(res.status).toBe(404)
  })

  it('GET /admin/bug-reports/reports/:id/screenshot returns image/png bytes', async () => {
    const id = await seedReport(app)
    const res = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}/screenshot`),
    )
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toBe('image/png')
    const buf = new Uint8Array(await res.arrayBuffer())
    expect(buf[0]).toBe(0x89)
    expect(buf[1]).toBe(0x50)
    expect(buf[2]).toBe(0x4e)
    expect(buf[3]).toBe(0x47)
  })

  it('returns 404 when screenshot is missing', async () => {
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/reports/bug-999/screenshot'),
    )
    expect(res.status).toBe(404)
  })

  it('PUT /reports/:id/status transitions and appends lifecycle', async () => {
    const id = await seedReport(app)
    const res = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}/status`, {
        method: 'PUT',
        body: JSON.stringify({
          status: 'fixed',
          fix_commit: 'a1b2c3',
          fix_description: 'patched',
        }),
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.status).toBe('fixed')
    expect(body.lifecycle.at(-1)).toMatchObject({
      action: 'status_changed',
      status: 'fixed',
      fix_commit: 'a1b2c3',
      fix_description: 'patched',
    })
  })

  it('PUT /reports/:id/status rejects unknown status with 422', async () => {
    const id = await seedReport(app)
    const res = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}/status`, {
        method: 'PUT',
        body: JSON.stringify({ status: 'wontfix' }),
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(422)
    const body = await res.json()
    expect(body.error).toBe('schema_error')
  })

  it('PUT /reports/:id/status returns 200 unchanged on idempotent no-op', async () => {
    const id = await seedReport(app)
    const res = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}/status`, {
        method: 'PUT',
        body: JSON.stringify({ status: 'open' }),
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.status).toBe('open')
    // Lifecycle should NOT have a duplicate status_changed entry —
    // adapter chose the no-op-collapse semantics.
    const transitions = body.lifecycle.filter(
      (e: { action: string }) => e.action === 'status_changed',
    )
    expect(transitions).toHaveLength(0)
  })

  it('respects can_edit_status: false (returns 403)', async () => {
    const altStorage = new MemoryStorage()
    const altApp = createBugFabApp({
      storage: altStorage,
      viewerPermissions: { can_edit_status: false },
    })
    const id = await seedReport(altApp)
    const res = await altApp.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}/status`, {
        method: 'PUT',
        body: JSON.stringify({ status: 'fixed' }),
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('DELETE /reports/:id returns 204 on success and 404 when re-deleted', async () => {
    const id = await seedReport(app)
    const res1 = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}`, { method: 'DELETE' }),
    )
    expect(res1.status).toBe(204)
    const res2 = await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}`, { method: 'DELETE' }),
    )
    expect(res2.status).toBe(404)
  })

  it('respects can_delete: false (returns 403)', async () => {
    const altStorage = new MemoryStorage()
    const altApp = createBugFabApp({
      storage: altStorage,
      viewerPermissions: { can_delete: false },
    })
    const id = await seedReport(altApp)
    const res = await altApp.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}`, { method: 'DELETE' }),
    )
    expect(res.status).toBe(403)
  })

  it('POST /bulk-close-fixed transitions all fixed → closed', async () => {
    const id1 = await seedReport(app)
    const id2 = await seedReport(app)
    for (const id of [id1, id2]) {
      await app.fetch(
        new Request(`http://test/admin/bug-reports/reports/${id}/status`, {
          method: 'PUT',
          body: JSON.stringify({ status: 'fixed' }),
          headers: { 'content-type': 'application/json' },
        }),
      )
    }
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/bulk-close-fixed', { method: 'POST' }),
    )
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.closed).toBe(2)
  })

  it('POST /bulk-archive-closed moves closed reports to archive', async () => {
    const id = await seedReport(app)
    await app.fetch(
      new Request(`http://test/admin/bug-reports/reports/${id}/status`, {
        method: 'PUT',
        body: JSON.stringify({ status: 'closed' }),
        headers: { 'content-type': 'application/json' },
      }),
    )
    const res = await app.fetch(
      new Request('http://test/admin/bug-reports/bulk-archive-closed', { method: 'POST' }),
    )
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.archived).toBe(1)

    // Default list excludes archived.
    const list = await app.fetch(new Request('http://test/admin/bug-reports/reports'))
    const listBody = await list.json()
    expect(listBody.total).toBe(0)

    // include_archived=true surfaces them again.
    const listAll = await app.fetch(
      new Request('http://test/admin/bug-reports/reports?include_archived=true'),
    )
    const listAllBody = await listAll.json()
    expect(listAllBody.total).toBe(1)
  })

  it('GET / returns HTML list page', async () => {
    await seedReport(app)
    const res = await app.fetch(new Request('http://test/admin/bug-reports/'))
    expect(res.status).toBe(200)
    const text = await res.text()
    expect(text).toMatch(/<!DOCTYPE html>/i)
    expect(text).toContain('Bug-Fab Reports')
  })
})

describe('config validation', () => {
  it('throws when viewerPrefix is empty', () => {
    expect(() =>
      createBugFabApp({ storage: new MemoryStorage(), viewerPrefix: '' }),
    ).toThrow(/viewerPrefix/)
  })

  it('throws when viewerPrefix is "/"', () => {
    expect(() =>
      createBugFabApp({ storage: new MemoryStorage(), viewerPrefix: '/' }),
    ).toThrow(/viewerPrefix/)
  })

  it('throws when storage is missing', () => {
    expect(() =>
      // @ts-expect-error intentionally missing storage
      createBugFabApp({}),
    ).toThrow(/storage/)
  })
})
