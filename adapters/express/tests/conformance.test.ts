// Conformance smoke test — boots the adapter, exercises the eight wire-protocol
// endpoints in sequence, and checks the response envelope shapes against
// docs/protocol-schema.json (loosely — full conformance comes from the
// upstream pytest plugin, which can target this same server when running CI
// with both Node and Python toolchains available).

import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import request from 'supertest'

import { buildHarness, TINY_PNG, validMetadata, type TestHarness } from './helpers.js'

const PREFIX = '/admin/bug-reports'

describe('conformance smoke (eight endpoints)', () => {
  let h: TestHarness
  let id: string

  beforeAll(async () => {
    h = buildHarness()
    const res = await request(h.app)
      .post(`${PREFIX}/bug-reports`)
      .field('metadata', JSON.stringify(validMetadata()))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })
    expect(res.status).toBe(201)
    id = res.body.id
  })

  afterAll(() => { h.cleanup() })

  it('1/8 POST /bug-reports — returns id, received_at, stored_at, github_issue_url', async () => {
    expect(id).toMatch(/^bug-\d{3,}$/)
  })

  it('2/8 GET /reports — returns {items, total, page, page_size, stats}', async () => {
    const res = await request(h.app).get(`${PREFIX}/reports`).set('Accept', 'application/json')
    expect(res.status).toBe(200)
    for (const k of ['items', 'total', 'page', 'page_size', 'stats']) {
      expect(res.body).toHaveProperty(k)
    }
    expect(res.body.stats).toMatchObject({
      open:          expect.any(Number),
      investigating: expect.any(Number),
      fixed:         expect.any(Number),
      closed:        expect.any(Number),
    })
  })

  it('3/8 GET /reports/:id — returns full BugReportDetail with required fields', async () => {
    const res = await request(h.app).get(`${PREFIX}/reports/${id}`).set('Accept', 'application/json')
    expect(res.status).toBe(200)
    for (const k of [
      'id', 'title', 'created_at', 'severity', 'status', 'report_type',
      'description', 'tags', 'reporter', 'context', 'lifecycle',
      'server_user_agent', 'client_reported_user_agent',
      'environment', 'client_ts', 'protocol_version', 'updated_at',
    ]) {
      expect(res.body).toHaveProperty(k)
    }
  })

  it('4/8 GET /reports/:id/screenshot — returns image/png', async () => {
    const res = await request(h.app).get(`${PREFIX}/reports/${id}/screenshot`)
    expect(res.status).toBe(200)
    expect(res.headers['content-type']).toBe('image/png')
  })

  it('5/8 PUT /reports/:id/status — transitions and returns updated detail', async () => {
    const res = await request(h.app)
      .put(`${PREFIX}/reports/${id}/status`)
      .send({ status: 'investigating' })
      .set('Content-Type', 'application/json')
    expect(res.status).toBe(200)
    expect(res.body.status).toBe('investigating')
  })

  it('6/8 POST /bulk-close-fixed — returns {closed: N}', async () => {
    const res = await request(h.app).post(`${PREFIX}/bulk-close-fixed`)
    expect(res.status).toBe(200)
    expect(res.body).toHaveProperty('closed')
    expect(typeof res.body.closed).toBe('number')
  })

  it('7/8 POST /bulk-archive-closed — returns {archived: N}', async () => {
    const res = await request(h.app).post(`${PREFIX}/bulk-archive-closed`)
    expect(res.status).toBe(200)
    expect(res.body).toHaveProperty('archived')
    expect(typeof res.body.archived).toBe('number')
  })

  it('8/8 DELETE /reports/:id — returns 204', async () => {
    const res = await request(h.app).delete(`${PREFIX}/reports/${id}`)
    expect(res.status).toBe(204)
  })
})

describe('conformance — error envelope shape', () => {
  it('every non-2xx response carries {error, detail}', async () => {
    const h = buildHarness()
    try {
      const responses = await Promise.all([
        request(h.app).get(`${PREFIX}/reports/bug-999`).set('Accept', 'application/json'),
        request(h.app).get(`${PREFIX}/reports?status=urgent`).set('Accept', 'application/json'),
        request(h.app).post(`${PREFIX}/bug-reports`),
      ])
      for (const r of responses) {
        expect(r.status).toBeGreaterThanOrEqual(400)
        expect(r.body).toHaveProperty('error')
        expect(r.body).toHaveProperty('detail')
        expect(typeof r.body.error).toBe('string')
      }
    } finally {
      h.cleanup()
    }
  })
})
