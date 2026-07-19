// GET /admin/bug-reports/reports — list bug reports with filters.
//
// PROTOCOL.md § "GET /reports". Query params: status, severity,
// environment, page, page_size, include_archived. Response is the
// paginated `BugReportListResponse` envelope (`items`, `total`, `page`,
// `page_size`, `stats`).

import { NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { checkAdminToken } from '@/lib/bug-fab/auth'
import type { ListFilters } from '@/lib/bug-fab/types'

export const runtime = 'nodejs' // FileStorage reads disk
export const dynamic = 'force-dynamic'

const PAGE_SIZE_DEFAULT = 20
const PAGE_SIZE_MAX = 200

export async function GET(req: Request): Promise<NextResponse> {
  const authError = checkAdminToken(req)
  if (authError !== null) return authError

  const url = new URL(req.url)
  const params = url.searchParams

  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1)
  const requestedSize = Number.parseInt(params.get('page_size') ?? String(PAGE_SIZE_DEFAULT), 10)
  const pageSize = Math.min(
    PAGE_SIZE_MAX,
    Math.max(1, Number.isFinite(requestedSize) ? requestedSize : PAGE_SIZE_DEFAULT),
  )

  const filters: ListFilters = {
    status: params.get('status') ?? undefined,
    severity: params.get('severity') ?? undefined,
    environment: params.get('environment') ?? undefined,
    module: params.get('module') ?? undefined,
    include_archived: params.get('include_archived') === 'true',
  }

  const result = await storage.listReports(filters, page, pageSize)
  return NextResponse.json(result)
}
