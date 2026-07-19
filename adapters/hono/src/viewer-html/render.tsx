/** @jsxImportSource hono/jsx */
// Viewer HTML rendering using Hono's built-in JSX runtime.
//
// Hono ships a JSX implementation that produces HTML strings without
// needing React. The runtime hint above (`jsxImportSource hono/jsx`)
// is recognized by both `tsc` and `vitest`'s esbuild transform, so this
// file compiles in either toolchain without a Babel dance.
//
// CSP nonce flow:
//   - app.ts captures a nonce per request via `opts.cspNonce`.
//   - That nonce is threaded through to `renderListPage` /
//     `renderDetailPage` here.
//   - The bundle script tag carries `nonce={nonce}` only when the
//     consumer wired the option; otherwise it's omitted and a strict
//     CSP will visibly refuse the script (intended fail-loud behavior).

import type { BugReportSummary, BugReportDetail, BugReportListStats } from '../types.js'

export interface RenderListProps {
  items: BugReportSummary[]
  total: number
  page: number
  pageSize: number
  stats: BugReportListStats
  bundleUrl: string
  detailUrlBase: string
  cspNonce: string | null
}

export interface RenderDetailProps {
  report: BugReportDetail
  bundleUrl: string
  screenshotUrl: string
  listUrl: string
  cspNonce: string | null
}

const STYLE = `
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 0;
         background: #fafbfc; color: #24292e; }
  header { background: #fff; border-bottom: 1px solid #e1e4e8; padding: 1rem 1.5rem;
           display: flex; align-items: center; justify-content: space-between; }
  h1 { margin: 0; font-size: 1.25rem; }
  main { padding: 1.5rem; max-width: 1100px; margin: 0 auto; }
  table { width: 100%; border-collapse: collapse; background: #fff;
          border: 1px solid #e1e4e8; border-radius: 6px; overflow: hidden; }
  th, td { text-align: left; padding: 0.6rem 0.9rem;
           border-bottom: 1px solid #eaecef; font-size: 0.9rem; }
  th { background: #f6f8fa; font-weight: 600; }
  tr:last-child td { border-bottom: none; }
  a { color: #0366d6; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 12px;
           font-size: 0.75rem; font-weight: 600; }
  .sev-low      { background: #c5def5; color: #032f62; }
  .sev-medium   { background: #fbca04; color: #24292e; }
  .sev-high     { background: #e4e669; color: #24292e; }
  .sev-critical { background: #b60205; color: #fff; }
  .stat-open          { color: #d73a49; }
  .stat-investigating { color: #b08800; }
  .stat-fixed         { color: #2cbe4e; }
  .stat-closed        { color: #6a737d; }
  pre { background: #f6f8fa; padding: 0.75rem; border-radius: 4px;
        overflow: auto; font-size: 0.8rem; }
  .stats-bar { display: flex; gap: 1.5rem; margin-bottom: 1rem; font-size: 0.9rem; }
  .stats-bar strong { font-weight: 600; }
  .empty { text-align: center; padding: 2rem; color: #6a737d; }
`

function severityClass(sev: string): string {
  return `badge sev-${sev}`
}

function statusClass(status: string): string {
  return `stat-${status}`
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function renderListPage(props: RenderListProps): string {
  const { items, total, page, pageSize, stats, bundleUrl, detailUrlBase, cspNonce } = props

  const rows = items.map((it) => (
    <tr key={it.id}>
      <td>
        <a href={`${detailUrlBase}/${it.id}/view`}>{it.id}</a>
      </td>
      <td>{it.title}</td>
      <td>
        <span class={severityClass(it.severity)}>{it.severity}</span>
      </td>
      <td>
        <span class={statusClass(it.status)}>{it.status}</span>
      </td>
      <td>{it.module || '—'}</td>
      <td>{it.created_at}</td>
    </tr>
  ))

  const body = (
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Bug-Fab — Reports</title>
        <style>{STYLE}</style>
      </head>
      <body>
        <header>
          <h1>Bug-Fab Reports</h1>
          <small>
            Page {page} · {items.length} of {total}
          </small>
        </header>
        <main>
          <div class="stats-bar">
            <span>
              <strong class="stat-open">{stats.open}</strong> open
            </span>
            <span>
              <strong class="stat-investigating">{stats.investigating}</strong> investigating
            </span>
            <span>
              <strong class="stat-fixed">{stats.fixed}</strong> fixed
            </span>
            <span>
              <strong class="stat-closed">{stats.closed}</strong> closed
            </span>
          </div>
          {items.length === 0 ? (
            <div class="empty">No reports yet.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Title</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th>Module</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
          )}
        </main>
        {cspNonce ? (
          <script src={bundleUrl} nonce={cspNonce} defer></script>
        ) : (
          <script src={bundleUrl} defer></script>
        )}
      </body>
    </html>
  )

  return `<!DOCTYPE html>${body.toString()}`
}

export function renderDetailPage(props: RenderDetailProps): string {
  const { report, bundleUrl, screenshotUrl, listUrl, cspNonce } = props

  const lifecycleRows = report.lifecycle.map((ev, idx) => (
    <tr key={idx}>
      <td>{ev.action}</td>
      <td>{ev.status ?? ''}</td>
      <td>{ev.by ?? ''}</td>
      <td>{ev.at}</td>
      <td>{ev.fix_commit ?? ''}</td>
    </tr>
  ))

  const body = (
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{`Bug-Fab — ${report.id}`}</title>
        <style>{STYLE}</style>
      </head>
      <body>
        <header>
          <h1>
            {report.id} — {report.title}
          </h1>
          <a href={listUrl}>← Back to list</a>
        </header>
        <main>
          <p>
            <span class={severityClass(report.severity)}>{report.severity}</span>{' '}
            <span class={statusClass(report.status)}>{report.status}</span>
          </p>
          <h2>Description</h2>
          <p>{report.description || <em>(none)</em>}</p>
          {report.expected_behavior ? (
            <>
              <h2>Expected behavior</h2>
              <p>{report.expected_behavior}</p>
            </>
          ) : null}
          <h2>Screenshot</h2>
          {report.has_screenshot ? (
            <img src={screenshotUrl} alt="screenshot" style="max-width: 100%; border: 1px solid #e1e4e8; border-radius: 4px;" />
          ) : (
            <em>(no screenshot)</em>
          )}
          <h2>Context</h2>
          <pre>{escapeHtml(JSON.stringify(report.context, null, 2))}</pre>
          <h2>Lifecycle</h2>
          <table>
            <thead>
              <tr>
                <th>Action</th>
                <th>Status</th>
                <th>By</th>
                <th>At</th>
                <th>Fix commit</th>
              </tr>
            </thead>
            <tbody>{lifecycleRows}</tbody>
          </table>
        </main>
        {cspNonce ? (
          <script src={bundleUrl} nonce={cspNonce} defer></script>
        ) : (
          <script src={bundleUrl} defer></script>
        )}
      </body>
    </html>
  )

  return `<!DOCTYPE html>${body.toString()}`
}
