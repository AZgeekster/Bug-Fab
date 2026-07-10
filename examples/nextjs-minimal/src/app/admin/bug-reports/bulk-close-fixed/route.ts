// POST /admin/bug-reports/bulk-close-fixed — close every `fixed` report.
//
// PROTOCOL.md § "POST /bulk-close-fixed". Idempotent at the per-report
// level — already-closed reports are not counted in the response.
// Response: { closed: <int> }.

import { NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { checkAdminToken } from '@/lib/bug-fab/auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function POST(req: Request): Promise<NextResponse> {
  const authError = checkAdminToken(req)
  if (authError !== null) return authError

  const closed = await storage.bulkCloseFixed()
  return NextResponse.json({ closed })
}
