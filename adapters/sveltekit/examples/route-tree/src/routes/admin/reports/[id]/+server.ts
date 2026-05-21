// GET / DELETE /admin/reports/[id]
//
// Two methods on the same route file — SvelteKit auto-discovers each named
// export.

import { createDetailHandler, createDeleteHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const GET = createDetailHandler(adapterOptions);
export const DELETE = createDeleteHandler(adapterOptions);
