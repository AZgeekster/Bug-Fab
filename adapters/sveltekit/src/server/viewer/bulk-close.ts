// POST /bulk-close-fixed — close all reports currently in "fixed" status.

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import type { BugFabAdapterOptions, BulkCloseResponse } from '../types.js';

export function createBulkCloseHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (_event: RequestEvent): Promise<Response> => {
    try {
      const closed = await opts.storage.bulkCloseFixed();
      const body: BulkCloseResponse = { closed };
      return json(body, { status: 200 });
    } catch (err) {
      console.error(`[bug-fab] bulkCloseFixed failed: ${err instanceof Error ? err.message : String(err)}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }
  };
}
