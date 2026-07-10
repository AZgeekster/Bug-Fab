// POST /admin/bug-reports/bulk-archive-closed — archive every `closed` report.
//
// PROTOCOL.md § "POST /bulk-archive-closed". For FileStorage the
// archive mechanism is moving the report directory into the `archive/`
// subfolder; archived reports are excluded from list responses unless
// `include_archived=true`. Response: { archived: <int> }.

import { NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { checkAdminToken } from '@/lib/bug-fab/auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function POST(req: Request): Promise<NextResponse> {
  const authError = checkAdminToken(req)
  if (authError !== null) return authError

  const archived = await storage.bulkArchiveClosed()
  return NextResponse.json({ archived })
}
