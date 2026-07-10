// GET /reports/[id] — fetch full detail.

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import { isValidReportId } from '../validation.js';
import type { BugFabAdapterOptions } from '../types.js';

export function createDetailHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const id = event.params.id;
    if (!isValidReportId(id)) {
      // 404, not 400: a malformed id is indistinguishable from a missing
      // report to a caller, and the shape guard must run before storage.
      return jsonError(Errors.notFound(String(id ?? '')), 404);
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
