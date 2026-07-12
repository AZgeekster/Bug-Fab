// GET / DELETE /admin/bug-reports/reports/{id} — detail + hard delete.
//
// PROTOCOL.md § "GET /reports/{id}" returns the full BugReportDetail.
// PROTOCOL.md § "DELETE /reports/{id}" returns 204 on success, 404
// when the report does not exist.

import { NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { checkAdminToken } from '@/lib/bug-fab/auth'
import { isValidReportId } from '@/lib/bug-fab/validation'
import { Errors } from '@/lib/bug-fab/errors'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

interface RouteContext {
  // Next.js 15 made dynamic route params a Promise — await before use.
  params: Promise<{ id: string }>
}

export async function GET(req: Request, { params }: RouteContext): Promise<NextResponse> {
  const authError = checkAdminToken(req)
  if (authError !== null) return authError

  const { id } = await params
  if (!isValidReportId(id)) {
    return NextResponse.json(Errors.notFound(id), { status: 404 })
  }
  const report = await storage.getReport(id)
  if (report === null) {
    return NextResponse.json(Errors.notFound(id), { status: 404 })
  }
  return NextResponse.json(report)
}

export async function DELETE(req: Request, { params }: RouteContext): Promise<NextResponse> {
  const authError = checkAdminToken(req)
  if (authError !== null) return authError

  const { id } = await params
  if (!isValidReportId(id)) {
    return NextResponse.json(Errors.notFound(id), { status: 404 })
  }
  const removed = await storage.deleteReport(id)
  if (!removed) {
    return NextResponse.json(Errors.notFound(id), { status: 404 })
  }
  // 204 — no body, per the protocol.
  return new NextResponse(null, { status: 204 })
}
