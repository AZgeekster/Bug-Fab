// DELETE /reports/[id] — hard delete (metadata + screenshot).

import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import type { BugFabAdapterOptions } from '../types.js';

export function createDeleteHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const id = event.params.id;
    if (!id) {
      return jsonError(Errors.validationError('id parameter is required'), 400);
    }

    try {
      // Verify existence first so we can return a typed 404.
      const existing = await opts.storage.getReport(id);
      if (!existing) {
        return jsonError(Errors.notFound(id), 404);
      }
      await opts.storage.deleteReport(id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('not found') || msg.includes('Report not found')) {
        return jsonError(Errors.notFound(id), 404);
      }
      console.error(`[bug-fab] deleteReport failed: ${msg}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }

    // 204 No Content.
    return new Response(null, { status: 204 });
  };
}
