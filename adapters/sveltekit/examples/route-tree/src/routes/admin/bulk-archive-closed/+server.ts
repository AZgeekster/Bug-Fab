// POST /admin/bulk-archive-closed
import { createBulkArchiveHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const POST = createBulkArchiveHandler(adapterOptions);
