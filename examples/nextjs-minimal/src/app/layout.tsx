import type { Metadata } from 'next'
import Script from 'next/script'
import './globals.css'

// Bug-Fab loads the vanilla bundle from /public/bug-fab/. The bundle is
// not committed to the repo — see public/bug-fab/README.md for how to
// fetch it before running the demo.

export const metadata: Metadata = {
  title: 'Bug-Fab — Next.js minimal example',
  description: 'POC demonstrating Bug-Fab integration as Next.js Route Handlers.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}

        {/*
          The Bug-Fab bundle is plain JS that boots itself on init().
          - `strategy="afterInteractive"` lets the page paint first.
          - We point `submitUrl` at our own Route Handler — the wire
            protocol is what makes this work; the frontend doesn't care
            that the backend is Next.js instead of FastAPI.
        */}
        <Script src="/bug-fab/bug-fab.js" strategy="afterInteractive" />
        <Script id="bug-fab-init" strategy="afterInteractive">
          {`
            window.addEventListener('DOMContentLoaded', function () {
              if (!window.BugFab) return;
              window.BugFab.init({
                submitUrl: '/api/bug-reports',
                appVersion: '0.1.0',
                environment: 'dev',
              });
            });
          `}
        </Script>
      </body>
    </html>
  )
}
