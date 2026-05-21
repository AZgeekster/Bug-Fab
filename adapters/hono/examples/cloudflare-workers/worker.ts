// Cloudflare Workers entry point.
//
// Bindings (configure in wrangler.toml):
//   - BUG_FAB_R2 — R2 bucket for screenshot blobs
//   - BUG_FAB_KV — KV namespace for metadata + counter
//   - GITHUB_PAT (secret, optional) — enables GitHub Issues sync
//
// Deploy: `wrangler deploy`

import { createBugFabApp, R2Storage } from 'bug-fab-hono'
import type { R2Bucket, KVNamespace } from 'bug-fab-hono/storage/r2'

interface Env {
  BUG_FAB_R2: R2Bucket
  BUG_FAB_KV: KVNamespace
  GITHUB_PAT?: string
  GITHUB_REPO?: string
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const storage = new R2Storage({
      bucket: env.BUG_FAB_R2,
      kv: env.BUG_FAB_KV,
    })

    const app = createBugFabApp({
      storage,
      submitPrefix: '/api',
      viewerPrefix: '/admin/bug-reports',
      github:
        env.GITHUB_PAT && env.GITHUB_REPO
          ? { enabled: true, pat: env.GITHUB_PAT, repo: env.GITHUB_REPO }
          : undefined,
      // Best-effort per-IP rate limit. Stronger protection lives on
      // the Cloudflare WAF / rate-limit dashboard.
      rateLimit: { enabled: true, maxRequests: 30, windowMs: 60_000 },
    })

    return app.fetch(req)
  },
}
