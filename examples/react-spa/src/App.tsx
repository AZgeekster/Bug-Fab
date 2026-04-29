/**
 * Demo app showing how to integrate Bug-Fab into a React SPA.
 *
 * The provider auto-mounts the floating action button on init, so you get
 * the FAB for free. The `useBugFab()` hook is for *programmatic* opens —
 * a menu item, keyboard shortcut, "Report this error" button on an error
 * boundary, etc.
 */

import { BugFabProvider, useBugFab } from "./BugFabProvider";

/**
 * Inner component that consumes the Bug-Fab context. Must be rendered
 * inside the provider, hence the split into App + AppContent.
 */
function AppContent(): JSX.Element {
  const { open, version, ready } = useBugFab();

  return (
    <main
      style={{
        fontFamily: "system-ui, sans-serif",
        maxWidth: 720,
        margin: "48px auto",
        padding: "0 16px",
        lineHeight: 1.5,
      }}
    >
      <h1>MyApp</h1>
      <p>
        This is a minimal React + Vite demo of the Bug-Fab integration. The
        floating bug icon in the bottom-right is rendered by the bundle
        automatically once the provider initializes.
      </p>

      <section style={{ marginTop: 32 }}>
        <h2>Programmatic open</h2>
        <p>
          The button below calls <code>useBugFab().open()</code> — useful for
          embedding the bug-report flow in a menu, an error boundary, or a
          keyboard shortcut.
        </p>
        <button
          type="button"
          onClick={open}
          disabled={!ready}
          style={{
            padding: "10px 18px",
            fontSize: 15,
            fontWeight: 500,
            background: ready ? "#f44336" : "#ccc",
            color: "#fff",
            border: "none",
            borderRadius: 4,
            cursor: ready ? "pointer" : "not-allowed",
          }}
        >
          Report a bug (programmatic)
        </button>
      </section>

      <section style={{ marginTop: 32 }}>
        <h2>Status</h2>
        <ul>
          <li>
            <strong>Ready:</strong> {ready ? "yes" : "loading bundle..."}
          </li>
          <li>
            <strong>Bundle version:</strong> {version || "(pending)"}
          </li>
        </ul>
      </section>

      <section style={{ marginTop: 32 }}>
        <h2>Try the auto-captured context</h2>
        <p>
          Click the buttons below to seed the console / network buffers, then
          open the bug report and expand <em>Auto-Captured Context</em> in the
          form panel to see them.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={() => console.error("Demo error: something went wrong")}
            style={demoBtn}
          >
            Trigger console.error
          </button>
          <button
            type="button"
            onClick={() => {
              fetch("/api/ping").catch(() => {
                /* expected: 404 in demo */
              });
            }}
            style={demoBtn}
          >
            Trigger network call
          </button>
        </div>
      </section>
    </main>
  );
}

const demoBtn: React.CSSProperties = {
  padding: "8px 14px",
  fontSize: 14,
  background: "#fff",
  color: "#212529",
  border: "1px solid #6c757d",
  borderRadius: 4,
  cursor: "pointer",
};

/**
 * Root component. Wraps the app with the Bug-Fab provider so the FAB and
 * the `useBugFab()` hook are available everywhere below.
 */
export default function App(): JSX.Element {
  return (
    <BugFabProvider
      config={{
        submitUrl: "/api/bug-reports",
        appVersion: "1.0.0",
        environment: "dev",
        onSubmitSuccess: (report) => {
          // eslint-disable-next-line no-console
          console.log("Bug report submitted:", report?.id);
        },
        onSubmitError: (err) => {
          // eslint-disable-next-line no-console
          console.warn("Bug report failed:", err.message);
        },
      }}
    >
      <AppContent />
    </BugFabProvider>
  );
}
