// HTML rendering helpers for the viewer index.
//
// Why hand-rolled HTML and not a Svelte template: factories must work
// regardless of which `+server.ts` mount the consumer wires up, and they
// must be callable from any runtime SvelteKit supports (Node, Vercel,
// Cloudflare, Bun). A `+page.svelte` would hard-bind the viewer index to
// one specific route and require Svelte at runtime; this approach keeps
// the package self-contained.
//
// The output mirrors the structure of the Python reference implementation's
// `bug_fab/templates/list.html` so the two adapters render comparably.

import type { BugReportSummary, BugReportListStats, ListFilters } from '../types.js';

export interface ListIndexViewModel {
  items: BugReportSummary[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  stats: BugReportListStats;
  filters: ListFilters;
  /** Permissions the viewer surface should expose. Defaults to a reasonable
   *  read-only view if unset by the consumer. */
  permissions: { canBulk: boolean; canEditStatus: boolean; canDelete: boolean };
}

// HTML escape — never trust storage values when interpolating into HTML.
// Covers the five core entities; numeric / enum values are still passed
// through because they originate from validated storage.
function esc(s: unknown): string {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function statCard(key: string, label: string, value: number): string {
  // `data-bug-fab-filter-status` is empty for "total" so the stat-card click
  // handler clears the filter, matching the Python template behavior.
  const filter = key === 'total' ? '' : key;
  return `
    <button
      type="button"
      class="bug-fab-stat bug-fab-stat-${esc(key)}"
      data-bug-fab-filter-status="${esc(filter)}"
      aria-label="Filter by ${esc(label)}"
    >
      <div class="bug-fab-stat-label">${esc(label)}</div>
      <div class="bug-fab-stat-value">${value}</div>
    </button>`;
}

function filterSelect(name: string, current: string, options: readonly string[]): string {
  const opts = options
    .map(
      (v) =>
        `<option value="${esc(v)}"${v === current ? ' selected' : ''}>${esc(
          v.charAt(0).toUpperCase() + v.slice(1)
        )}</option>`
    )
    .join('');
  return `
    <label>
      ${esc(name.charAt(0).toUpperCase() + name.slice(1))}
      <select name="${esc(name)}">
        <option value="">All</option>
        ${opts}
      </select>
    </label>`;
}

function row(item: BugReportSummary): string {
  return `
    <tr data-bug-fab-detail-href="${esc(item.id)}">
      <td><span class="bug-fab-mono">${esc(item.id)}</span></td>
      <td>${esc(item.title)}</td>
      <td><span class="bug-fab-badge bug-fab-sev-${esc(item.severity)}">${esc(item.severity)}</span></td>
      <td><span class="bug-fab-badge bug-fab-status-${esc(item.status)}">${esc(item.status)}</span></td>
      <td>${esc(item.module || '-')}</td>
      <td><span class="bug-fab-mono">${esc(item.created_at)}</span></td>
    </tr>`;
}

function pagination(page: number, totalPages: number, qsBase: string): string {
  if (totalPages <= 1) return '';
  let out = `<nav class="bug-fab-pagination" aria-label="Pagination">`;
  if (page > 1) {
    out += `<a class="bug-fab-button" href="${qsBase}&page=${page - 1}">Prev</a>`;
  }
  for (let n = 1; n <= totalPages; n++) {
    if (n === page) {
      out += `<span class="bug-fab-button bug-fab-page-current">${n}</span>`;
    } else {
      out += `<a class="bug-fab-button" href="${qsBase}&page=${n}">${n}</a>`;
    }
  }
  if (page < totalPages) {
    out += `<a class="bug-fab-button" href="${qsBase}&page=${page + 1}">Next</a>`;
  }
  out += `</nav>`;
  return out;
}

/** Render the HTML list page. Mirrors `bug_fab/templates/list.html`. */
export function renderListIndex(vm: ListIndexViewModel): string {
  const f = vm.filters;
  const filterStatus = (f.status as string | undefined) ?? '';
  const filterSeverity = (f.severity as string | undefined) ?? '';
  const filterEnvironment = f.environment ?? '';

  const stats = [
    statCard('open', 'Open', vm.stats.open),
    statCard('investigating', 'Investigating', vm.stats.investigating),
    statCard('fixed', 'Fixed', vm.stats.fixed),
    statCard('closed', 'Closed', vm.stats.closed),
    statCard('total', 'Total', vm.total)
  ].join('');

  const tableBody = vm.items.length
    ? `<table class="bug-fab-table" aria-label="Bug reports">
        <thead>
          <tr>
            <th>ID</th><th>Title</th><th>Severity</th>
            <th>Status</th><th>Module</th><th>Created</th>
          </tr>
        </thead>
        <tbody>${vm.items.map(row).join('')}</tbody>
      </table>`
    : `<div class="bug-fab-table-empty">No bug reports yet. The first submission will appear here.</div>`;

  const qsBase =
    `?status=${encodeURIComponent(filterStatus)}` +
    `&severity=${encodeURIComponent(filterSeverity)}` +
    `&environment=${encodeURIComponent(filterEnvironment)}`;

  const bulkButtons = vm.permissions.canBulk
    ? `<button type="button" class="bug-fab-button" data-bug-fab-bulk="close-fixed">Close all fixed</button>
       <button type="button" class="bug-fab-button" data-bug-fab-bulk="archive-closed">Archive closed</button>`
    : '';

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bug Reports - Bug-Fab</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem; background: #f8fafc; color: #1f2937; }
    .bug-fab-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
    .bug-fab-actions { display: flex; gap: 0.5rem; }
    .bug-fab-button { padding: 0.45rem 0.9rem; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; text-decoration: none; color: #1f2937; cursor: pointer; }
    .bug-fab-button-primary { background: #2563eb; color: #fff; border-color: #2563eb; }
    .bug-fab-stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.5rem; margin-bottom: 1rem; }
    .bug-fab-stat { padding: 0.75rem; border: 1px solid #e2e8f0; background: #fff; border-radius: 4px; cursor: pointer; }
    .bug-fab-stat-label { font-size: 0.85rem; color: #64748b; }
    .bug-fab-stat-value { font-size: 1.5rem; font-weight: 600; }
    .bug-fab-card { background: #fff; padding: 1rem; border: 1px solid #e2e8f0; border-radius: 4px; margin-bottom: 1rem; }
    .bug-fab-filters { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: end; }
    .bug-fab-filters label { display: flex; flex-direction: column; font-size: 0.85rem; }
    .bug-fab-table { width: 100%; border-collapse: collapse; }
    .bug-fab-table th, .bug-fab-table td { padding: 0.55rem 0.5rem; text-align: left; border-bottom: 1px solid #e2e8f0; }
    .bug-fab-table tr { cursor: pointer; }
    .bug-fab-table tr:hover { background: #f1f5f9; }
    .bug-fab-table-empty { padding: 1rem; color: #64748b; }
    .bug-fab-mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.9em; }
    .bug-fab-badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.8rem; background: #e2e8f0; }
    .bug-fab-pagination { display: flex; gap: 0.25rem; margin-top: 1rem; }
    .bug-fab-page-current { background: #2563eb; color: #fff; }
  </style>
</head>
<body>
  <div class="bug-fab-header">
    <h1>Bug Reports</h1>
    <div class="bug-fab-actions">
      ${bulkButtons}
      <button type="button" class="bug-fab-button" data-bug-fab-action="reload">Refresh</button>
    </div>
  </div>

  <section class="bug-fab-stats" aria-label="Status counts">${stats}</section>

  <form class="bug-fab-card bug-fab-filters" method="get" id="bug-fab-filter-form">
    ${filterSelect('status', filterStatus, ['open', 'investigating', 'fixed', 'closed'])}
    ${filterSelect('severity', filterSeverity, ['low', 'medium', 'high', 'critical'])}
    <label>
      Environment
      <input type="text" name="environment" value="${esc(filterEnvironment)}" placeholder="any" />
    </label>
    <button type="submit" class="bug-fab-button bug-fab-button-primary">Apply</button>
    <a href="?" class="bug-fab-button">Clear</a>
  </form>

  <div class="bug-fab-card">
    ${tableBody}
    ${pagination(vm.page, vm.totalPages, qsBase)}
  </div>

  <script>
    (function () {
      // Refresh button.
      document.querySelectorAll("[data-bug-fab-action='reload']").forEach(function (button) {
        button.addEventListener("click", function () { window.location.reload(); });
      });

      // Stat-card click filters by status.
      document.querySelectorAll("[data-bug-fab-filter-status]").forEach(function (el) {
        el.addEventListener("click", function () {
          var params = new URLSearchParams(window.location.search);
          var target = el.getAttribute("data-bug-fab-filter-status");
          if (target) { params.set("status", target); } else { params.delete("status"); }
          params.delete("page");
          window.location.search = params.toString();
        });
      });

      // Row click drills into detail page.
      document.querySelectorAll("[data-bug-fab-detail-href]").forEach(function (row) {
        var id = row.getAttribute("data-bug-fab-detail-href");
        row.addEventListener("click", function (event) {
          if (event.target.closest("a, button, input, select")) return;
          var base = window.location.pathname.endsWith("/")
            ? window.location.pathname
            : window.location.pathname + "/";
          window.location.href = base + "reports/" + id;
        });
      });

      // Bulk action buttons.
      var bulkBase = window.location.pathname.endsWith("/")
        ? window.location.pathname
        : window.location.pathname + "/";
      document.querySelectorAll("[data-bug-fab-bulk]").forEach(function (button) {
        button.addEventListener("click", async function () {
          var action = button.getAttribute("data-bug-fab-bulk");
          if (!confirm("Run bulk action: " + action + "?")) return;
          var url = bulkBase + (action === "close-fixed" ? "bulk-close-fixed" : "bulk-archive-closed");
          try {
            var resp = await fetch(url, { method: "POST" });
            if (!resp.ok) { alert("Bulk action failed (" + resp.status + ")"); return; }
            var data = await resp.json();
            var count = data.closed != null ? data.closed : (data.archived != null ? data.archived : 0);
            alert("Done: " + count + " reports affected");
            setTimeout(function () { window.location.reload(); }, 700);
          } catch (err) {
            alert("Bulk action error");
          }
        });
      });
    })();
  </script>
</body>
</html>`;
}
