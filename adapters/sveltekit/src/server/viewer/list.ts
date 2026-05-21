// GET /reports — list reports with filters + pagination.
//
// Wire shape: { items, total, page, page_size, stats }.
// Default page=1, page_size=20, max page_size=200.

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import {
  isValidStatus,
  isValidSeverity,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE
} from '../validation.js';
import type { BugFabAdapterOptions, BugReportListResponse, ListFilters } from '../types.js';

function intParam(value: string | null, fallback: number, min: number, max: number): number {
  if (value === null) return fallback;
  const n = parseInt(value, 10);
  if (Number.isNaN(n) || n < min) return fallback;
  return Math.min(n, max);
}

export function createListHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const url = event.url;

    // Build filters with strict enum validation — the deprecated-values rule
    // applies to READ paths: we should NOT 422 on unknown statuses passed as
    // filter values, since they may be valid historical statuses. Instead,
    // pass them through to storage and let the backend either return zero
    // matches or honor them. This matches the protocol's deprecated-values
    // contract.
    const filters: ListFilters = {
      include_archived: url.searchParams.get('include_archived') === 'true'
    };

    const status = url.searchParams.get('status');
    if (status !== null) filters.status = status as ListFilters['status'];
    const severity = url.searchParams.get('severity');
    if (severity !== null) filters.severity = severity as ListFilters['severity'];
    const environment = url.searchParams.get('environment');
    if (environment !== null) filters.environment = environment;

    const page = intParam(url.searchParams.get('page'), 1, 1, Number.MAX_SAFE_INTEGER);
    const pageSize = intParam(url.searchParams.get('page_size'), DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE);

    try {
      const { items, total, stats } = await opts.storage.listReports(filters, page, pageSize);
      const body: BugReportListResponse = {
        items,
        total,
        page,
        page_size: pageSize,
        stats
      };
      return json(body, { status: 200 });
    } catch (err) {
      console.error(`[bug-fab] listReports failed: ${err instanceof Error ? err.message : String(err)}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }
  };
}

// Re-export for convenience: validators in case the consumer wants to apply
// stricter enum validation client-side. They aren't enforced here on the
// server — see PROTOCOL.md § "deprecated-values rule".
export { isValidStatus, isValidSeverity };
