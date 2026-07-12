// GET /admin/bug-reports/reports/{id}/screenshot — raw PNG bytes.
//
// PROTOCOL.md § "GET /reports/{id}/screenshot" — returns the screenshot
// as `image/png`. Reads from disk via FileStorage; serves with private
// cache headers. 404 with NO body when the report or its file is missing
// (the binary endpoint is exempt from the JSON error envelope per the
// protocol's response-shape table).

import { NextResponse } from 'next/server'
import { readFile } from 'node:fs/promises'
import { storage } from '@/lib/bug-fab/storage'
import { checkAdminToken } from '@/lib/bug-fab/auth'
import { isValidReportId } from '@/lib/bug-fab/validation'

// WHY runtime = 'nodejs': we read from disk via node:fs. Edge runtime
// has no filesystem access, so this would silently 500 on Edge.
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
    return new NextResponse(null, { status: 404 })
  }
  const filePath = await storage.getScreenshotPath(id)
  if (filePath === null) {
    return new NextResponse(null, { status: 404 })
  }
  const bytes = await readFile(filePath)
  // NextResponse needs an Uint8Array (or BodyInit-compatible) — Buffer
  // is a Uint8Array subclass so this is a no-op cast at runtime.
  return new NextResponse(new Uint8Array(bytes), {
    status: 200,
    headers: {
      'Content-Type': 'image/png',
      'Content-Length': String(bytes.length),
      'Cache-Control': 'private, max-age=300',
    },
  })
}
