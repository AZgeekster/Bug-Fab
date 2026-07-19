// PUT /reports/[id]/status — update status, append lifecycle entry.
//
// IMPORTANT: status enum is enforced strictly on WRITE paths (no silent
// coercion). Read paths (list, detail) honor the deprecated-values rule
// and pass unknowns through.

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import { isValidReportId, validateStatusUpdate } from '../validation.js';
import { syncStatusToGitHub } from '../github.js';
import type { BugFabAdapterOptions, StatusUpdateRequest } from '../types.js';

export function createStatusHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const id = event.params.id;
    if (!isValidReportId(id)) {
      // 404, not 400: a malformed id is indistinguishable from a missing
      // report to a caller, and the shape guard must run before storage.
      return jsonError(Errors.notFound(String(id ?? '')), 404);
    }

    let parsed: unknown;
    try {
      parsed = await event.request.json();
    } catch (err) {
      return jsonError(
        Errors.validationError(`request body is not valid JSON: ${err instanceof Error ? err.message : String(err)}`),
        400
      );
    }

    const result = validateStatusUpdate(parsed);
    if (!result.ok) {
      return jsonError(Errors.schemaError(result.errors), 422);
    }

    const body = parsed as StatusUpdateRequest;

    // Resolve actor identity for the lifecycle audit log. The server-derived
    // value (if any) is trusted over anything the client supplies, per
    // PROTOCOL.md § "Lifecycle audit log".
    let by: string | null = null;
    if (opts.resolveActor) {
      try {
        by = await opts.resolveActor(event);
      } catch {
        by = null;
      }
    }

    let detail;
    try {
      detail = await opts.storage.updateStatus(id, body.status, by, body.fix_commit, body.fix_description);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('not found') || msg.includes('Report not found')) {
        return jsonError(Errors.notFound(id), 404);
      }
      console.error(`[bug-fab] updateStatus failed: ${msg}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }

    // Best-effort GitHub status sync.
    if (opts.github?.enabled) {
      void syncStatusToGitHub(detail, opts.github).catch((err) => {
        console.warn(
          `[bug-fab] GitHub status sync threw: ${err instanceof Error ? err.message : String(err)}`
        );
      });
    }

    return json(detail, { status: 200 });
  };
}
