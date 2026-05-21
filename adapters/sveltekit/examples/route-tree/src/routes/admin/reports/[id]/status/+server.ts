// PUT /admin/reports/[id]/status
import { createStatusHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const PUT = createStatusHandler(adapterOptions);
