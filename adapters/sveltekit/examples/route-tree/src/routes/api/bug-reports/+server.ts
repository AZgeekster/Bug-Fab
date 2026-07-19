// POST /api/bug-reports — Bug-Fab intake.
//
// This is mounted at `/api/` so the consumer's auth middleware can leave it
// open (typical pattern: anyone can submit a bug, only admins can view).
//
// CSRF NOTE: SvelteKit's built-in CSRF protection rejects cross-origin POSTs
// with a non-form Content-Type. Multipart uploads from the Bug-Fab JS bundle
// are same-origin in the typical case, so this works out of the box. If your
// app accepts submissions from arbitrary origins (e.g., a separate
// admin-tools domain submitting bugs), you'll need to disable CSRF for THIS
// route. SvelteKit doesn't have per-route CSRF toggles, but you can:
//   1. Configure `csrf.checkOrigin: false` globally in svelte.config.js (then
//      handle CSRF yourself in `hooks.server.ts`), OR
//   2. Set `Origin` to the same value as `Host` in your client request.
// See repo/docs/ADAPTERS.md § "Common pitfalls (SvelteKit)".

import { createIntakeHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const POST = createIntakeHandler(adapterOptions);
