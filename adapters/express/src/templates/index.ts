// Tiny server-side HTML templating helpers for the Bug-Fab viewer.
//
// We deliberately avoid a real template engine — the viewer's needs are
// modest enough that a few hand-rolled functions keep dependencies down.
// The official Python adapter ships a richer Jinja-templated viewer; this
// adapter renders a simpler list/detail pair that is functionally
// equivalent for the v0.1 wire protocol's needs.
//
// MOUNT-PREFIX INVARIANT (load-bearing):
//   `mountPath` in this module is the *resolved* per-request mount, captured
//   from `req.baseUrl` at the call site in `viewer.ts`. It is the absolute
//   path under which the router was mounted (e.g. `/admin/bug-reports` for
//   `app.use('/admin/bug-reports', router)`, or `''` for `app.use('/', ...)`).
//   All template URLs are composed as `${mountPath}/...` so the rendered
//   HTML carries absolute paths under any mount prefix. Do NOT pass an
//   empty constant here from a non-root mount — that breaks every link.
//   See `viewer.ts` mount-path capture and the tests in
//   `tests/viewer.test.ts` § "viewer — mount-path templating".

import type {
  BugReportSummary,
  BugReportListStats,
  BugReportDetail,
} from '../types.js'

function escape(s: unknown): string {
  return String(s ?? '')
    .replaceAll('&',  '&amp;')
    .replaceAll('<',  '&lt;')
    .replaceAll('>',  '&gt;')
    .replaceAll('"',  '&quot;')
    .replaceAll("'",  '&#39;')
}

const COMMON_STYLE = `
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 1.5rem;
         color: #1f2933; background: #f7fafc; }
  h1, h2 { margin-top: 0; }
  a { color: #1565c0; }
  table { border-collapse: collapse; width: 100%; background: white; }
  th, td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #e2e8f0; text-align: left;
           font-size: 0.9rem; }
  th { background: #edf2f7; font-weight: 600; }
  .stats { display: flex; gap: 0.75rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .stat { background: white; padding: 0.5rem 1rem; border-radius: 4px;
          border: 1px solid #e2e8f0; font-size: 0.9rem; }
  .stat strong { display: block; font-size: 1.25rem; }
  .badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px;
           font-size: 0.75rem; font-weight: 600; }
  .sev-low { background: #c5def5; color: #1c3d5a; }
  .sev-medium { background: #fbca04; color: #5b4500; }
  .sev-high { background: #e4e669; color: #5b5a00; }
  .sev-critical { background: #b60205; color: white; }
  .status-open { color: #c53030; font-weight: 600; }
  .status-investigating { color: #d97706; font-weight: 600; }
  .status-fixed { color: #2f855a; font-weight: 600; }
  .status-closed { color: #4a5568; }
  pre { background: #edf2f7; padding: 0.75rem; border-radius: 4px;
        white-space: pre-wrap; word-break: break-word; }
  .meta-row { margin-bottom: 0.5rem; }
  .meta-row span { color: #4a5568; }
  img.shot { max-width: 100%; border: 1px solid #cbd5e0; border-radius: 4px; }
`

function shell(title: string, body: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escape(title)} — Bug-Fab</title>
  <style>${COMMON_STYLE}</style>
</head>
<body>
${body}
</body>
</html>`
}

export function renderListPage(args: {
  mountPath:   string
  items:       BugReportSummary[]
  stats:       BugReportListStats
  total:       number
  page:        number
  pageSize:    number
}): string {
  const { mountPath, items, stats, total, page, pageSize } = args

  const rows = items.map((it) => `
    <tr>
      <td><a href="${escape(mountPath)}/reports/${escape(it.id)}">${escape(it.id)}</a></td>
      <td>${escape(it.title)}</td>
      <td><span class="badge sev-${escape(it.severity)}">${escape(it.severity)}</span></td>
      <td><span class="status-${escape(it.status)}">${escape(it.status)}</span></td>
      <td>${escape(it.module ?? '')}</td>
      <td>${escape(it.created_at)}</td>
    </tr>`).join('')

  const body = `
    <h1>Bug Reports</h1>
    <div class="stats">
      <div class="stat"><strong>${stats.open}</strong>open</div>
      <div class="stat"><strong>${stats.investigating}</strong>investigating</div>
      <div class="stat"><strong>${stats.fixed}</strong>fixed</div>
      <div class="stat"><strong>${stats.closed}</strong>closed</div>
      <div class="stat"><strong>${total}</strong>total (page ${page}, size ${pageSize})</div>
    </div>
    <table>
      <thead>
        <tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Module</th><th>Created</th></tr>
      </thead>
      <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:#718096;">No reports yet.</td></tr>'}</tbody>
    </table>
  `

  return shell('Bug Reports', body)
}

export function renderDetailPage(args: {
  mountPath: string
  detail:    BugReportDetail
}): string {
  const { mountPath, detail } = args
  const tags = detail.tags.map((t) => `<span class="badge" style="background:#edf2f7;">${escape(t)}</span>`).join(' ')
  const lifecycle = detail.lifecycle.map((e) => `
    <li><strong>${escape(e.action)}</strong> by ${escape(e.by)} at ${escape(e.at)}${
      e.status ? ` → <em>${escape(e.status)}</em>` : ''
    }</li>`).join('')

  const body = `
    <p><a href="${escape(mountPath)}">&larr; All reports</a></p>
    <h1>${escape(detail.title)}</h1>
    <div class="meta-row"><span>ID:</span> ${escape(detail.id)}</div>
    <div class="meta-row"><span>Status:</span> <span class="status-${escape(detail.status)}">${escape(detail.status)}</span></div>
    <div class="meta-row"><span>Severity:</span> <span class="badge sev-${escape(detail.severity)}">${escape(detail.severity)}</span></div>
    <div class="meta-row"><span>Created:</span> ${escape(detail.created_at)}</div>
    <div class="meta-row"><span>Tags:</span> ${tags || '<em>(none)</em>'}</div>
    <h2>Description</h2>
    <pre>${escape(detail.description) || '<em>(no description)</em>'}</pre>
    ${detail.expected_behavior ? `<h2>Expected behavior</h2><pre>${escape(detail.expected_behavior)}</pre>` : ''}
    <h2>Screenshot</h2>
    <img class="shot" src="${escape(mountPath)}/reports/${escape(detail.id)}/screenshot" alt="Screenshot for ${escape(detail.id)}">
    <h2>Lifecycle</h2>
    <ul>${lifecycle}</ul>
    <h2>Context</h2>
    <pre>${escape(JSON.stringify(detail.context, null, 2))}</pre>
  `

  return shell(`${detail.id} — ${detail.title}`, body)
}
