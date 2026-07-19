// Simple HTML viewer at /admin/bug-reports.
//
// This is a server component that reads the storage layer directly — no
// HTTP round-trip — so the rendered page is a snapshot of the index at
// request time. Production deployments would either render via the
// JSON list endpoint (so an SPA viewer can poll/refresh) or replace this
// with the bundled Python viewer at `/admin/bug-reports/` from the
// FastAPI reference.
//
// Auth: this page does NOT call `checkAdminToken` because the auth
// helper is HTTP-header-based and Next.js page components don't have
// the same request access shape as Route Handlers. In a real
// deployment, gate this page via `middleware.ts` with a path matcher
// for `/admin/bug-reports/*`. For the POC, the page is unauthenticated
// so it renders for the local dev session.

import { storage } from '@/lib/bug-fab/storage'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export default async function AdminListPage() {
  const result = await storage.listReports({}, 1, 50)

  return (
    <main>
      <h1>Bug-Fab admin viewer</h1>
      <p>
        {result.total} report{result.total === 1 ? '' : 's'}. Showing page{' '}
        {result.page} (size {result.page_size}).
      </p>
      <p>
        Stats — open: {result.stats.open}; investigating: {result.stats.investigating};{' '}
        fixed: {result.stats.fixed}; closed: {result.stats.closed}.
      </p>

      {result.items.length === 0 ? (
        <p>
          <em>No reports yet.</em> Open <a href="/">the demo page</a> and click the bug icon.
        </p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid currentColor' }}>
              <th style={{ padding: '0.4rem 0.6rem' }}>ID</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Title</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Severity</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Status</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Created</th>
            </tr>
          </thead>
          <tbody>
            {result.items.map((item) => (
              <tr key={item.id} style={{ borderBottom: '1px solid rgba(127,127,127,0.25)' }}>
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  <code>{item.id}</code>
                </td>
                <td style={{ padding: '0.4rem 0.6rem' }}>{item.title}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>{item.severity}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>{item.status}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>{item.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p style={{ marginTop: '2rem' }}>
        Raw JSON list: <a href="/admin/bug-reports/reports">/admin/bug-reports/reports</a>
      </p>
    </main>
  )
}
