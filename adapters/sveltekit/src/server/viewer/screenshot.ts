// GET /reports/[id]/screenshot — return raw PNG bytes.
//
// Per PROTOCOL.md, the response body is binary `image/png`. We use a Response
// with a Uint8Array body rather than streaming a file — SvelteKit's `Response`
// supports both, but reading bytes through IStorage is the portable path
// (works for FileStorage AND DrizzleStorage with object-storage backends).

import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import type { BugFabAdapterOptions } from '../types.js';

export function createScreenshotHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const id = event.params.id;
    if (!id) {
      return jsonError(Errors.validationError('id parameter is required'), 400);
    }

    let bytes: Uint8Array | null;
    try {
      bytes = await opts.storage.getScreenshotBytes(id);
    } catch (err) {
      console.error(`[bug-fab] getScreenshotBytes failed: ${err instanceof Error ? err.message : String(err)}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }

    if (!bytes) {
      return jsonError(Errors.notFound(id), 404);
    }

    return new Response(bytes, {
      status: 200,
      headers: {
        'Content-Type': 'image/png',
        'Content-Length': String(bytes.byteLength),
        'Cache-Control': 'private, max-age=300'
      }
    });
  };
}
