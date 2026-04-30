// Demo home page. The point of the POC is the floating bug icon in the
// bottom-right corner of every page (loaded by the Bug-Fab bundle in
// app/layout.tsx). This page just provides somewhere to click around.

export default function HomePage() {
  return (
    <main>
      <h1>MyApp (Bug-Fab Next.js demo)</h1>
      <p>
        This page has nothing to demo on its own. The point is the small bug
        icon in the bottom-right corner. Click it, draw on the screenshot
        if you like, fill in a title, and submit. The report lands in{' '}
        <code>./bug_reports/</code>.
      </p>

      <h2>Verify the protocol</h2>
      <ul>
        <li>
          Reports land at{' '}
          <a href="/admin/bug-reports/reports">/admin/bug-reports/reports</a> as JSON.
        </li>
        <li>
          A given report&apos;s screenshot is at{' '}
          <code>/admin/bug-reports/reports/&lt;id&gt;/screenshot</code>.
        </li>
        <li>
          The simple HTML viewer is at{' '}
          <a href="/admin/bug-reports">/admin/bug-reports</a>.
        </li>
      </ul>

      <h2>What this proves</h2>
      <p>
        Next.js can be its own Bug-Fab adapter. All eight wire-protocol
        endpoints are implemented as App Router Route Handlers — no separate
        backend process. See <code>README.md</code> for architecture notes
        and conformance verification.
      </p>
    </main>
  )
}
