// Vercel Edge Function entry point.
//
// File location: `api/[[...slug]].ts` in the consumer's Next.js / Vercel
// project. An example deploy on Vercel Edge — copy this into the
// consumer's project and adjust the storage backend.
//
// IMPORTANT: Vercel Edge caps request body at 4.5 MiB. Bug-Fab's default
// screenshot ceiling is 10 MiB. Either:
//   (a) keep screenshots small (html2canvas at scale: 0.6 typically lands
//       under 4 MiB even for full-page captures), OR
//   (b) move to a Node.js serverless function with a higher cap, OR
//   (c) deploy the collector on Cloudflare Workers Paid (~100 MiB cap).

import { createBugFabApp, MemoryStorage } from 'bug-fab-hono'

export const config = {
  runtime: 'edge',
}

// MemoryStorage is for the example only — Vercel Edge invocations are
// ephemeral, so a real deployment needs a hosted store (Vercel KV,
// Upstash Redis, Neon Postgres). Implement the IStorage interface
// against your store of choice.
const app = createBugFabApp({
  storage: new MemoryStorage(),
})

export default function handler(req: Request): Response | Promise<Response> {
  return app.fetch(req)
}
