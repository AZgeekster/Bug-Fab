// GET /admin/reports — list reports (JSON).
//
// The HTML viewer page uses `+page.server.ts` to fetch this data via a
// `load()` function. See `+page.server.ts` next to this file for the SSR
// pattern. Keeping the JSON endpoint as a separate `+server.ts` lets API
// clients query it directly while the HTML page reuses the same data shape.

import { createListHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const GET = createListHandler(adapterOptions);
