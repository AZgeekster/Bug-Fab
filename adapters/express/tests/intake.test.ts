import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import request from 'supertest'

import { buildHarness, TINY_PNG, TINY_JPEG, validMetadata, type TestHarness } from './helpers.js'

describe('POST /bug-reports — intake', () => {
  let h: TestHarness

  beforeEach(() => { h = buildHarness() })
  afterEach(() => { h.cleanup() })

  it('accepts a valid multipart submission and returns 201 with id/received_at/stored_at', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata()))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(201)
    expect(res.body).toMatchObject({
      id:               expect.stringMatching(/^bug-\d{3,}$/),
      received_at:      expect.any(String),
      stored_at:        expect.any(String),
      github_issue_url: null,
    })
    // Intake response MUST NOT echo user-submitted free text at the top level
    expect(res.body).not.toHaveProperty('title')
    expect(res.body).not.toHaveProperty('description')
  })

  it('rejects JPEG with 415 unsupported_media_type', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata()))
      .attach('screenshot', TINY_JPEG, { filename: 'shot.jpg', contentType: 'image/jpeg' })

    expect(res.status).toBe(415)
    expect(res.body.error).toBe('unsupported_media_type')
  })

  it('rejects severity "urgent" with 422 schema_error (no silent coercion)', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({ severity: 'urgent' })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(422)
    expect(res.body.error).toBe('schema_error')
    expect(res.body.detail).toMatch(/severity must be one of/)
  })

  it('rejects unknown protocol_version with 400 unsupported_protocol_version', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({ protocol_version: '0.99' })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(400)
    expect(res.body.error).toBe('unsupported_protocol_version')
  })

  it('rejects missing protocol_version with 400 unsupported_protocol_version', async () => {
    const meta = validMetadata()
    delete (meta as Record<string, unknown>).protocol_version
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(meta))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(400)
    expect(res.body.error).toBe('unsupported_protocol_version')
  })

  it('rejects missing client_ts with 422 schema_error', async () => {
    const meta = validMetadata()
    delete (meta as Record<string, unknown>).client_ts
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(meta))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(422)
    expect(res.body.error).toBe('schema_error')
    expect(res.body.detail).toMatch(/client_ts/)
  })

  it('rejects empty title with 422 schema_error', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({ title: '   ' })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(422)
    expect(res.body.error).toBe('schema_error')
    expect(res.body.detail).toMatch(/title is required/)
  })

  it('rejects oversized title (>200 chars) with 422 schema_error', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({ title: 'x'.repeat(201) })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(422)
    expect(res.body.detail).toMatch(/title must not exceed/)
  })

  it('rejects reporter.email > 256 chars with 422 schema_error', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({
        reporter: { email: 'x'.repeat(257) },
      })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(422)
    expect(res.body.detail).toMatch(/reporter\.email/)
  })

  it('rejects missing screenshot with 400 validation_error', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata()))

    expect(res.status).toBe(400)
    expect(res.body.error).toBe('validation_error')
  })

  it('rejects malformed metadata JSON with 400 validation_error', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', '{not json')
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(400)
    expect(res.body.error).toBe('validation_error')
    expect(res.body.detail).toMatch(/not valid JSON/)
  })

  it('rejects invalid report_type with 422 schema_error', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({ report_type: 'bogus' })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(422)
    expect(res.body.detail).toMatch(/report_type must be one of/)
  })

  it('captures server User-Agent independently from client value', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .set('User-Agent', 'Server-Captured/1.0')
      .field('metadata', JSON.stringify(validMetadata({
        context: { url: 'x', user_agent: 'Client-Spoofed/9.9' },
      })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(201)
    const id = res.body.id

    const detail = await request(h.app).get(`/admin/bug-reports/reports/${id}`).set('Accept', 'application/json')
    expect(detail.body.server_user_agent).toBe('Server-Captured/1.0')
    expect(detail.body.client_reported_user_agent).toBe('Client-Spoofed/9.9')
  })

  it('preserves arbitrary extra keys on context (extra="allow")', async () => {
    const res = await request(h.app)
      .post('/admin/bug-reports/bug-reports')
      .field('metadata', JSON.stringify(validMetadata({
        context: { url: 'x', custom_field: 'hello', nested: { deep: 42 } },
      })))
      .attach('screenshot', TINY_PNG, { filename: 'shot.png', contentType: 'image/png' })

    expect(res.status).toBe(201)
    const detail = await request(h.app)
      .get(`/admin/bug-reports/reports/${res.body.id}`)
      .set('Accept', 'application/json')
    expect(detail.body.context.custom_field).toBe('hello')
    expect(detail.body.context.nested).toEqual({ deep: 42 })
  })
})
