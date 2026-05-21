// POST /bulk-archive-closed — archive all reports currently in "closed" status.

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import type { BugFabAdapterOptions, BulkArchiveResponse } from '../types.js';

export function createBulkArchiveHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (_event: RequestEvent): Promise<Response> => {
    try {
      const archived = await opts.storage.bulkArchiveClosed();
      const body: BulkArchiveResponse = { archived };
      return json(body, { status: 200 });
    } catch (err) {
      console.error(`[bug-fab] bulkArchiveClosed failed: ${err instanceof Error ? err.message : String(err)}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }
  };
}
