// GET /admin/reports/[id]/screenshot
import { createScreenshotHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';

export const GET = createScreenshotHandler(adapterOptions);
