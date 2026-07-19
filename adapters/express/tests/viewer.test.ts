import { mkdtempSync, rmSync } from 'node:fs'
import { join as pathJoin } from 'node:path'
import { tmpdir } from 'node:os'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import request from 'supertest'
import express from 'express'

import { buildHarness, TINY_PNG, validMetadata, type TestHarness } from './helpers.js'
import { createBugFabRouter } from '../src/index.js'
import { FileStorage } from '../src/storage/FileStorage.js'

async function submit(h: TestHarness, overrides: Record<string, unknown> = {}): Promise<string> {
  const res = await request(h.app)
    .post('/admin/bug-reports/bug-reports')
    .field('metadata', JSON.stringify(validMetadata(overrides)))
    .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })
  if (res.status !== 201) throw new Error(`submit failed: ${res.status} ${JSON.stringify(res.body)}`)
  return res.body.id as string
}

describe('viewer — list, detail, screenshot', () => {
  let h: TestHarness

  beforeEach(() => { h = buildHarness() })
  afterEach(() => { h.cleanup() })

  it('GET / returns HTML list at the mount root', async () => {
    await submit(h)
    const res = await request(h.app).get('/admin/bug-reports').set('Accept', 'text/html')
    expect(res.status).toBe(200)
    expect(res.headers['content-type']).toMatch(/text\/html/)
    expect(res.text).toMatch(/Bug Reports/)
  })

  it('GET /reports returns paginated JSON envelope with stats', async () => {
    await submit(h, { title: 'A' })
    await submit(h, { title: 'B', severity: 'low' })
    const res = await request(h.app).get('/admin/bug-reports/reports').set('Accept', 'application/json')

    expect(res.status).toBe(200)
    expect(res.body.items).toHaveLength(2)
    expect(res.body.total).toBe(2)
    expect(res.body.page).toBe(1)
    expect(res.body.page_size).toBe(20)
    expect(res.body.stats).toMatchObject({ open: 2, investigating: 0, fixed: 0, closed: 0 })
  })

  it('GET /reports?status=fixed filters and rejects invalid enum with 422', async () => {
    await submit(h)
    const ok = await request(h.app).get('/admin/bug-reports/reports?status=fixed').set('Accept', 'application/json')
    expect(ok.status).toBe(200)
    expect(ok.body.items).toHaveLength(0)

    const bad = await request(h.app).get('/admin/bug-reports/reports?status=urgent').set('Accept', 'application/json')
    expect(bad.status).toBe(422)
    expect(bad.body.error).toBe('schema_error')
  })

  it('GET /reports/:id returns full BugReportDetail JSON', async () => {
    const id = await submit(h)
    const res = await request(h.app).get(`/admin/bug-reports/reports/${id}`).set('Accept', 'application/json')

    expect(res.status).toBe(200)
    expect(res.body).toMatchObject({
      id,
      title:            'Save button broken',
      severity:         'high',
      status:           'open',
      report_type:      'bug',
      protocol_version: '0.1',
      client_ts:        '2026-04-30T15:30:00-07:00',
    })
    expect(Array.isArray(res.body.lifecycle)).toBe(true)
    expect(res.body.lifecycle[0]).toMatchObject({ action: 'created' })
  })

  it('GET /reports/:id with Accept: text/html renders the detail page', async () => {
    const id = await submit(h)
    const res = await request(h.app).get(`/admin/bug-reports/reports/${id}`).set('Accept', 'text/html')
    expect(res.status).toBe(200)
    expect(res.headers['content-type']).toMatch(/text\/html/)
    expect(res.text).toContain(id)
  })

  it('GET /reports/:id 404s on missing report', async () => {
    const res = await request(h.app).get('/admin/bug-reports/reports/bug-999').set('Accept', 'application/json')
    expect(res.status).toBe(404)
    expect(res.body.error).toBe('not_found')
  })

  it('GET /reports/:id/screenshot returns image/png bytes', async () => {
    const id = await submit(h)
    const res = await request(h.app).get(`/admin/bug-reports/reports/${id}/screenshot`)
    expect(res.status).toBe(200)
    expect(res.headers['content-type']).toBe('image/png')
    expect(res.body.length).toBe(TINY_PNG.length)
  })
})

describe('viewer — status update', () => {
  let h: TestHarness
  beforeEach(() => { h = buildHarness() })
  afterEach(() => { h.cleanup() })

  it('PUT /reports/:id/status transitions and appends lifecycle entry', async () => {
    const id = await submit(h)
    const res = await request(h.app)
      .put(`/admin/bug-reports/reports/${id}/status`)
      .send({ status: 'fixed', fix_commit: 'abc123', fix_description: 'restored event listener' })
      .set('Content-Type', 'application/json')

    expect(res.status).toBe(200)
    expect(res.body.status).toBe('fixed')
    expect(res.body.lifecycle).toHaveLength(2)
    expect(res.body.lifecycle[1]).toMatchObject({
      action:          'status_changed',
      status:          'fixed',
      fix_commit:      'abc123',
      fix_description: 'restored event listener',
    })
  })

  it('PUT /reports/:id/status rejects unknown status with 422', async () => {
    const id = await submit(h)
    const res = await request(h.app)
      .put(`/admin/bug-reports/reports/${id}/status`)
      .send({ status: 'wat' })
      .set('Content-Type', 'application/json')

    expect(res.status).toBe(422)
    expect(res.body.error).toBe('schema_error')
  })

  it('PUT /reports/:id/status is idempotent on same-status no-op', async () => {
    const id = await submit(h)
    const res = await request(h.app)
      .put(`/admin/bug-reports/reports/${id}/status`)
      .send({ status: 'open' })
      .set('Content-Type', 'application/json')

    expect(res.status).toBe(200)
    expect(res.body.lifecycle).toHaveLength(1)
  })
})

describe('viewer — delete + bulk ops', () => {
  let h: TestHarness
  beforeEach(() => { h = buildHarness() })
  afterEach(() => { h.cleanup() })

  it('DELETE /reports/:id returns 204 then 404 on subsequent fetch', async () => {
    const id = await submit(h)
    const del = await request(h.app).delete(`/admin/bug-reports/reports/${id}`)
    expect(del.status).toBe(204)

    const get = await request(h.app).get(`/admin/bug-reports/reports/${id}`).set('Accept', 'application/json')
    expect(get.status).toBe(404)
  })

  it('POST /bulk-close-fixed closes all fixed reports', async () => {
    const id1 = await submit(h)
    const id2 = await submit(h, { title: 'B' })
    await request(h.app).put(`/admin/bug-reports/reports/${id1}/status`).send({ status: 'fixed' })
    await request(h.app).put(`/admin/bug-reports/reports/${id2}/status`).send({ status: 'fixed' })

    const res = await request(h.app).post('/admin/bug-reports/bulk-close-fixed')
    expect(res.status).toBe(200)
    expect(res.body.closed).toBe(2)

    const list = await request(h.app).get('/admin/bug-reports/reports?status=closed').set('Accept', 'application/json')
    expect(list.body.items).toHaveLength(2)
  })

  it('POST /bulk-archive-closed moves closed reports out of the default list', async () => {
    const id = await submit(h)
    await request(h.app).put(`/admin/bug-reports/reports/${id}/status`).send({ status: 'fixed' })
    await request(h.app).post('/admin/bug-reports/bulk-close-fixed')

    const res = await request(h.app).post('/admin/bug-reports/bulk-archive-closed')
    expect(res.status).toBe(200)
    expect(res.body.archived).toBe(1)

    const visible = await request(h.app).get('/admin/bug-reports/reports').set('Accept', 'application/json')
    expect(visible.body.items).toHaveLength(0)

    // Drift D fix (audit 2026-05-01): archived reports MUST be returned
    // when `include_archived=true`. The previous implementation silently
    // dropped them; FileStorage now keeps a parallel archivedIndex that is
    // merged in here.
    const archived = await request(h.app)
      .get('/admin/bug-reports/reports?include_archived=true')
      .set('Accept', 'application/json')
    expect(archived.status).toBe(200)
    expect(Array.isArray(archived.body.items)).toBe(true)
    expect(archived.body.items).toHaveLength(1)
    expect(archived.body.items[0].id).toBe(id)
    expect(archived.body.total).toBe(1)
  })
})

describe('viewer — permissions gating', () => {
  it('omits status/delete/bulk routes when permissions are false', async () => {
    const h = buildHarness({
      viewerPermissions: {
        can_edit_status: false,
        can_delete:      false,
        can_bulk:        false,
      },
    })
    try {
      const id = await submit(h)
      const put = await request(h.app)
        .put(`/admin/bug-reports/reports/${id}/status`)
        .send({ status: 'fixed' })
        .set('Content-Type', 'application/json')
      expect(put.status).toBe(404)

      const del = await request(h.app).delete(`/admin/bug-reports/reports/${id}`)
      expect(del.status).toBe(404)

      const bulk = await request(h.app).post('/admin/bug-reports/bulk-close-fixed')
      expect(bulk.status).toBe(404)
    } finally {
      h.cleanup()
    }
  })
})

// Drift C regression suite (audit 2026-05-01) — the rendered HTML viewer's
// links and image sources must point under whatever prefix the router was
// mounted at. The previous implementation passed an empty string at
// construction time, which produced root-absolute paths like
// `<a href="/reports/bug-001">` instead of
// `<a href="/admin/bug-reports/reports/bug-001">`. The fix uses
// `req.baseUrl` to capture the runtime mount path. These tests guard
// against regression by booting fresh harnesses under non-trivial mounts
// and asserting the rendered href / src attributes.

function buildHarnessAt(mountPath: string): TestHarness {
  const storageDir = mkdtempSync(pathJoin(tmpdir(), 'bug-fab-express-mount-test-'))
  const storage = new FileStorage({ storageDir })
  const app = express()
  app.use(mountPath, createBugFabRouter({ storage }))
  return {
    app,
    storage,
    storageDir,
    cleanup: () => rmSync(storageDir, { recursive: true, force: true }),
  }
}

async function submitTo(h: TestHarness, mount: string, overrides: Record<string, unknown> = {}): Promise<string> {
  const res = await request(h.app)
    .post(`${mount}/bug-reports`)
    .field('metadata', JSON.stringify(validMetadata(overrides)))
    .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })
  if (res.status !== 201) throw new Error(`submit failed: ${res.status} ${JSON.stringify(res.body)}`)
  return res.body.id as string
}

describe('viewer — mount-path templating (Drift C regression)', () => {
  it('list HTML row links resolve under a single-segment non-root mount', async () => {
    const mount = '/admin/bug-reports'
    const h = buildHarnessAt(mount)
    try {
      const id = await submitTo(h, mount)
      const res = await request(h.app).get(mount).set('Accept', 'text/html')
      expect(res.status).toBe(200)
      expect(res.headers['content-type']).toMatch(/text\/html/)
      // Row link must carry the absolute mount-prefixed path, not a
      // root-absolute `/reports/...` that would 404 outside the mount.
      expect(res.text).toContain(`href="${mount}/reports/${id}"`)
    } finally {
      h.cleanup()
    }
  })

  it('list HTML row links resolve under a multi-segment nested mount', async () => {
    const mount = '/api/v2/internal/bugs'
    const h = buildHarnessAt(mount)
    try {
      const id = await submitTo(h, mount)
      const res = await request(h.app).get(mount).set('Accept', 'text/html')
      expect(res.status).toBe(200)
      expect(res.text).toContain(`href="${mount}/reports/${id}"`)
      // Negative assertion: the broken pre-fix output looked like
      // `href="/reports/${id}"` (root-absolute, no mount).
      expect(res.text).not.toContain(`href="/reports/${id}"`)
    } finally {
      h.cleanup()
    }
  })

  it('detail HTML "All reports" back-link and screenshot src use the mount prefix', async () => {
    const mount = '/admin/bug-reports'
    const h = buildHarnessAt(mount)
    try {
      const id = await submitTo(h, mount)
      const res = await request(h.app).get(`${mount}/reports/${id}`).set('Accept', 'text/html')
      expect(res.status).toBe(200)
      // Back-link to the list page.
      expect(res.text).toContain(`href="${mount}"`)
      // Screenshot src — must be the mount-prefixed absolute path so the
      // browser fetches /admin/bug-reports/reports/<id>/screenshot, not
      // /reports/<id>/screenshot.
      expect(res.text).toContain(`src="${mount}/reports/${id}/screenshot"`)
    } finally {
      h.cleanup()
    }
  })

  it('detail HTML screenshot src works under a multi-segment nested mount', async () => {
    const mount = '/api/v2/internal/bugs'
    const h = buildHarnessAt(mount)
    try {
      const id = await submitTo(h, mount)
      const res = await request(h.app).get(`${mount}/reports/${id}`).set('Accept', 'text/html')
      expect(res.status).toBe(200)
      expect(res.text).toContain(`src="${mount}/reports/${id}/screenshot"`)
      expect(res.text).not.toContain(`src="/reports/${id}/screenshot"`)
    } finally {
      h.cleanup()
    }
  })
})

// Path-traversal guard.
//
// Every `:id` route fed `req.params.id` straight into a storage lookup and,
// for the screenshot route, into a filesystem join. There was no shape guard
// anywhere in this adapter -- `grep isValidReportId src/` returned nothing.
//
// The storage backend is booby-trapped rather than merely asserting 404:
// a real FileStorage simply returns null for `bug-nonsense`, so the route
// 404s either way and the test would pass with the guard deleted.
describe('viewer — report id shape guard', () => {
  const MALFORMED = [
    'bug-traversal-attempt',
    'not-a-bug-id',
    'bug-',
    'bug-001.png',
    'bug-1234567890123', // 13 digits, one past the bound
  ]

  function trapHarness(): TestHarness {
    // Any read is a guard failure. Proxy so every method name traps.
    const trap = new Proxy({}, {
      get: (_t, name: string) => async () => {
        throw new Error(`storage.${name} called with an unvalidated report id`)
      },
    })
    const app = express()
    app.use('/admin/bug-reports', createBugFabRouter({
      storage: trap as unknown as FileStorage,
      viewerPermissions: { canEditStatus: true, canDelete: true, canBulk: true },
    }))
    return { app, storage: trap as unknown as FileStorage, storageDir: '', cleanup: () => {} }
  }

  for (const bad of MALFORMED) {
    it(`rejects ${bad} before storage is touched`, async () => {
      const h = trapHarness()
      const base = '/admin/bug-reports'

      expect((await request(h.app).get(`${base}/reports/${bad}`)).status).toBe(404)
      expect((await request(h.app).get(`${base}/reports/${bad}/screenshot`)).status).toBe(404)
      expect((await request(h.app).delete(`${base}/reports/${bad}`)).status).toBe(404)
      expect(
        (await request(h.app).put(`${base}/reports/${bad}/status`).send({ status: 'fixed' })).status,
      ).toBe(404)
    })
  }

  it('still serves a well-formed id', async () => {
    const h = buildHarness()
    try {
      const id = await submit(h)
      expect(/^bug-[A-Za-z]?\d{1,12}$/.test(id)).toBe(true)
      const res = await request(h.app).get(`/admin/bug-reports/reports/${id}`)
      expect(res.status).toBe(200)
    } finally {
      h.cleanup()
    }
  })
})
