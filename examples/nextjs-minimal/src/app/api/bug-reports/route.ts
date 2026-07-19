// POST /api/bug-reports — Bug-Fab intake endpoint.
//
// Implements PROTOCOL.md § "POST /bug-reports — submit a report":
//   1. Parse multipart body (`metadata` JSON + `screenshot` PNG).
//   2. Enforce 10 MiB cap on the screenshot.
//   3. PNG magic-byte check (rejects JPEG, etc.).
//   4. Validate metadata against the v0.1 schema.
//   5. Capture the SERVER user-agent from the request header — never
//      trust the client-supplied value as authoritative.
//   6. Persist via FileStorage and return the minimal intake envelope.

import { NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { isValidPngBuffer, validateSubmission } from '@/lib/bug-fab/validation'
import { Errors } from '@/lib/bug-fab/errors'

// WHY runtime = 'nodejs': the default Edge runtime cannot read large
// buffers and cannot touch node:fs (FileStorage writes to disk).
export const runtime = 'nodejs'
// WHY dynamic: this handler reads request body and headers; static
// optimization would be incorrect.
export const dynamic = 'force-dynamic'

const SCREENSHOT_MAX_BYTES = 10 * 1024 * 1024 // PROTOCOL.md § Size limits

export async function POST(req: Request): Promise<NextResponse> {
  let form: FormData
  try {
    form = await req.formData()
  } catch {
    return NextResponse.json(Errors.validationError('could not parse multipart body'), { status: 400 })
  }

  const metadataRaw = form.get('metadata')
  const screenshotEntry = form.get('screenshot')
  if (typeof metadataRaw !== 'string' || !(screenshotEntry instanceof File)) {
    return NextResponse.json(
      Errors.validationError('metadata and screenshot are both required'),
      { status: 400 },
    )
  }

  const screenshotBuf = Buffer.from(await screenshotEntry.arrayBuffer())
  if (screenshotBuf.length === 0) {
    return NextResponse.json(Errors.validationError('screenshot is empty'), { status: 400 })
  }
  if (screenshotBuf.length > SCREENSHOT_MAX_BYTES) {
    return NextResponse.json(Errors.payloadTooLarge(SCREENSHOT_MAX_BYTES), { status: 413 })
  }
  if (!isValidPngBuffer(screenshotBuf)) {
    return NextResponse.json(
      Errors.unsupportedMediaType('screenshot must be a PNG image (PNG magic bytes not found)'),
      { status: 415 },
    )
  }

  let metadata: unknown
  try {
    metadata = JSON.parse(metadataRaw)
  } catch (err) {
    const detail = err instanceof Error ? err.message : 'invalid JSON'
    return NextResponse.json(Errors.validationError(`metadata is not valid JSON: ${detail}`), {
      status: 400,
    })
  }

  const result = validateSubmission(metadata)
  if (!result.ok) {
    return NextResponse.json(result.envelope, { status: result.status })
  }

  // PROTOCOL.md § User-Agent trust boundary — the request-header
  // User-Agent is the source of truth; the client value lives on for
  // diagnostics but never overwrites it.
  const serverUserAgent = req.headers.get('user-agent') ?? ''

  let reportId: string
  try {
    reportId = await storage.saveReport(
      { ...result.value, server_user_agent: serverUserAgent },
      screenshotBuf,
    )
  } catch (err) {
    console.error('bug_fab_storage_save_failed', err)
    return NextResponse.json(Errors.internalError('failed to persist bug report'), { status: 500 })
  }

  // GitHub sync would slot in here (see comment in lib/bug-fab/storage.ts).
  // Out of scope for the POC — `github_issue_url` always null.

  // PROTOCOL.md § Response — minimal envelope, NEVER echo user text.
  return NextResponse.json(
    {
      id: reportId,
      received_at: new Date().toISOString(),
      stored_at: `bug-fab-nextjs://${reportId}`,
      github_issue_url: null,
    },
    { status: 201 },
  )
}
