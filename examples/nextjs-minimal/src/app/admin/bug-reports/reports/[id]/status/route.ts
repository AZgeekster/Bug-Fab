// PUT /admin/bug-reports/reports/{id}/status — update status.
//
// PROTOCOL.md § "PUT /reports/{id}/status":
//   - Body: { status: <enum>, fix_commit?, fix_description? }
//   - Status enum is locked — invalid values MUST 422 (no silent coerce).
//   - Appends a `status_changed` lifecycle event to the report.
//   - Returns the full updated detail object on success.

import { NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { checkAdminToken } from '@/lib/bug-fab/auth'
import { isValidReportId, validateStatusUpdate } from '@/lib/bug-fab/validation'
import { Errors } from '@/lib/bug-fab/errors'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

interface RouteContext {
  // Next.js 15 made dynamic route params a Promise — await before use.
  params: Promise<{ id: string }>
}

export async function PUT(req: Request, { params }: RouteContext): Promise<NextResponse> {
  const authError = checkAdminToken(req)
  if (authError !== null) return authError

  const { id } = await params
  if (!isValidReportId(id)) {
    return NextResponse.json(Errors.notFound(id), { status: 404 })
  }

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json(Errors.validationError('request body must be valid JSON'), {
      status: 400,
    })
  }

  const result = validateStatusUpdate(body)
  if (!result.ok) {
    return NextResponse.json(result.envelope, { status: 422 })
  }

  const updated = await storage.updateStatus(
    id,
    result.value.status,
    result.value.fix_commit ?? '',
    result.value.fix_description ?? '',
  )
  if (updated === null) {
    return NextResponse.json(Errors.notFound(id), { status: 404 })
  }

  // GitHub sync would propagate fixed/closed here. Best-effort, never fail.
  return NextResponse.json(updated)
}
