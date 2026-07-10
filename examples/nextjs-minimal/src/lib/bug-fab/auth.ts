// Placeholder admin auth for the Next.js POC.
//
// The Bug-Fab v0.1 protocol intentionally ships NO auth abstraction —
// consumers protect viewer routes by mounting them under a URL prefix
// their existing auth middleware already covers (PROTOCOL.md § Auth —
// mount-point delegation).
//
// This POC has no real auth so it boots without setup. We compare an
// `x-admin-token` request header against the `ADMIN_TOKEN` env var and
// reject with 401 on mismatch. Production deployments MUST replace this
// with a real solution: NextAuth.js, Clerk, your own session middleware,
// or — to inherit the consumer's existing auth — Next.js
// `middleware.ts` with a path matcher for `/admin/bug-reports/*`.

import { NextResponse } from 'next/server'

/**
 * Returns null when the request is authorized, or a NextResponse with
 * 401 when it isn't. Route Handlers call this at the top of every
 * viewer endpoint — Route Handlers don't share Express-style middleware
 * so each handler must guard itself.
 *
 * If `ADMIN_TOKEN` is unset, the POC auto-allows requests (so a fresh
 * `npm run dev` works without any env file). This is deliberately
 * convenient for local dev and deliberately wrong for production —
 * see the comment block above.
 */
export function checkAdminToken(req: Request): NextResponse | null {
  const expected = process.env.ADMIN_TOKEN
  if (!expected) {
    // Dev-mode escape hatch — see file header. Production sets ADMIN_TOKEN.
    return null
  }
  const provided = req.headers.get('x-admin-token')
  if (provided === expected) return null
  return NextResponse.json(
    { error: 'validation_error', detail: 'admin authentication required' },
    { status: 401 },
  )
}
