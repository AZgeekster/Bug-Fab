// POST /admin/reports/bulk-close-fixed
import { createBulkCloseHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const POST = createBulkCloseHandler(adapterOptions);
