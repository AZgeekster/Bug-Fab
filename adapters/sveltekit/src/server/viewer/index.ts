// GET <viewer-prefix> — HTML list page for the viewer mount root.
//
// The Adapter Authorship Checklist (item 6, mount-prefix invariant) requires
// that the viewer's mount root serve an HTML list page so a user navigating
// to `/admin/reports` (or whatever prefix the consumer chose) sees a
// human-readable index, not a 404.
//
// The Python reference (`bug_fab/routers/viewer.py:list_reports_html`) ships
// this as a Jinja-rendered HTML response. SvelteKit consumers wire this
// factory into a `+page.server.ts` (or a `+server.ts` that returns
// text/html) at the same prefix the JSON endpoints sit under:
//
//     // src/routes/admin/reports/+server.ts
//     import { createViewerIndexHandler } from 'bug-fab-sveltekit/server';
//     import { adapterOptions } from '$lib/server/bug-fab';
//     export const GET = createViewerIndexHandler(adapterOptions);
//
// Returns text/html rather than JSON; `Accept: application/json` callers
// should hit the sibling `<prefix>/reports` endpoint instead.

import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from '../errors.js';
import {
  isValidStatus,
  isValidSeverity,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE
} from '../validation.js';
import { renderListIndex } from './_html.js';
import type { BugFabAdapterOptions, ListFilters } from '../types.js';

function intParam(value: string | null, fallback: number, min: number, max: number): number {
  if (value === null) return fallback;
  const n = parseInt(value, 10);
  if (Number.isNaN(n) || n < min) return fallback;
  return Math.min(n, max);
}

export function createViewerIndexHandler(opts: BugFabAdapterOptions): RequestHandler {
  return async (event: RequestEvent): Promise<Response> => {
    const url = event.url;

    // Read paths honor the deprecated-values rule — pass unknown enum
    // values through to storage rather than 422-ing. Same posture as
    // `createListHandler`.
    const filters: ListFilters = {
      include_archived: url.searchParams.get('include_archived') === 'true'
    };
    const status = url.searchParams.get('status');
    if (status !== null && status !== '') filters.status = status as ListFilters['status'];
    const severity = url.searchParams.get('severity');
    if (severity !== null && severity !== '') filters.severity = severity as ListFilters['severity'];
    const environment = url.searchParams.get('environment');
    if (environment !== null && environment !== '') filters.environment = environment;

    const page = intParam(url.searchParams.get('page'), 1, 1, Number.MAX_SAFE_INTEGER);
    const pageSize = intParam(
      url.searchParams.get('page_size'),
      DEFAULT_PAGE_SIZE,
      1,
      MAX_PAGE_SIZE
    );

    try {
      const { items, total, stats } = await opts.storage.listReports(filters, page, pageSize);
      const totalPages = Math.max(Math.ceil(total / pageSize), 1);

      const html = renderListIndex({
        items,
        total,
        page,
        pageSize,
        totalPages,
        stats,
        filters,
        // Defaults match the Python reference's `viewer_permissions`. A
        // future BugFabAdapterOptions.viewerPermissions can override this.
        permissions: { canBulk: true, canEditStatus: true, canDelete: true }
      });

      return new Response(html, {
        status: 200,
        headers: { 'content-type': 'text/html; charset=utf-8' }
      });
    } catch (err) {
      console.error(
        `[bug-fab] viewer index render failed: ${err instanceof Error ? err.message : String(err)}`
      );
      return jsonError(Errors.storageUnavailable(), 503);
    }
  };
}

// Re-export for parity with `createListHandler` — useful for consumers that
// want to apply stricter filter validation themselves.
export { isValidStatus, isValidSeverity };
