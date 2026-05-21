// GET /reports/[id] — fetch full detail.

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import type { BugFabAdapterOptions } from '../types.js';

export function createDetailHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const id = event.params.id;
    if (!id) {
      return jsonError(Errors.validationError('id parameter is required'), 400);
    }

    try {
      const detail = await opts.storage.getReport(id);
      if (!detail) {
        return jsonError(Errors.notFound(id), 404);
      }
      return json(detail, { status: 200 });
    } catch (err) {
      console.error(`[bug-fab] getReport failed: ${err instanceof Error ? err.message : String(err)}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }
  };
}
