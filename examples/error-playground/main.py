"""FastAPI app for the public Bug-Fab POC.

This is the heavier sibling of ``examples/fastapi-minimal/main.py``. It
adds a row of "trigger an error" buttons and two intentionally-broken
endpoints so visitors to the hosted POC have something to break before
clicking the FAB. Each button trips one of the bundle's capture paths
(``window.onerror``, ``console.error``, ``unhandledrejection``, fetch
hook), so the resulting bug report carries real auto-captured context
without anyone having to fabricate it.

Wiring is the same as the minimal example. Storage location is read
from ``BUG_FAB_STORAGE_DIR`` so the same image works locally, on
Fly.io with a mounted volume, or on a Hugging Face Space.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import bug_fab
from bug_fab.routers import submit, viewer

STORAGE_DIR = Path(
    os.environ.get(
        "BUG_FAB_STORAGE_DIR",
        str(Path(__file__).resolve().parent / "bug_reports"),
    )
)


def _resolve_static_dir() -> Path:
    package_root = Path(bug_fab.__file__).resolve().parent
    for candidate in (package_root / "static", package_root.parent / "static"):
        if (candidate / "bug-fab.js").is_file():
            return candidate
    raise FileNotFoundError(f"bug-fab.js bundle not found near {package_root}")


def create_app() -> FastAPI:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Bug-Fab POC")
    storage = bug_fab.FileStorage(storage_dir=STORAGE_DIR)
    submit.configure(storage=storage)
    app.include_router(submit.submit_router, prefix="/api")
    app.include_router(viewer.viewer_router, prefix="/admin/bug-reports")
    app.mount(
        "/bug-fab/static",
        StaticFiles(directory=str(_resolve_static_dir())),
        name="bug-fab-static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return DEMO_PAGE

    @app.get("/demo/missing")
    async def demo_missing() -> dict:
        # Always-404 endpoint for the "Failing fetch (404)" button — the
        # fetch hook in the bundle records the response in network_log.
        raise HTTPException(status_code=404, detail="this resource does not exist")

    @app.get("/demo/explode")
    async def demo_explode() -> dict:
        # Always-500 endpoint for the "Failing fetch (500)" button.
        # Raising bare RuntimeError bypasses the HTTPException envelope so
        # uvicorn returns a real Internal Server Error.
        raise RuntimeError("intentional demo crash")

    return app


DEMO_PAGE = r"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Bug-Fab POC</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { font-family: system-ui, sans-serif; max-width: 720px; margin: 3rem auto; padding: 0 1rem; color: #212529; line-height: 1.5; }
      code { background: #f1f3f5; padding: 0.1rem 0.35rem; border-radius: 3px; }
      a { color: #1971c2; }
      .badge { display: inline-block; background: #fff3bf; color: #5c3c00; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.85em; }
      h2 { margin-top: 2.25rem; }
      .demo-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 0.6rem;
        margin: 1rem 0;
      }
      .demo-grid button {
        font: inherit;
        padding: 0.6rem 0.75rem;
        border: 1px solid #ffa8a8;
        background: #fff5f5;
        color: #842029;
        border-radius: 6px;
        cursor: pointer;
        text-align: left;
      }
      .demo-grid button:hover { background: #ffe3e3; }
      .demo-grid button .what { display: block; font-size: 0.82em; color: #6a4040; margin-top: 0.15rem; }
      #demo-log {
        margin-top: 0.75rem;
        font-family: ui-monospace, Consolas, monospace;
        font-size: 0.85em;
        background: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 4px;
        padding: 0.5rem 0.75rem;
        min-height: 1.5em;
        white-space: pre-wrap;
        word-break: break-word;
      }
      #demo-log:empty::before { content: "(no events yet \u2014 click a red button)"; color: #868e96; font-style: italic; }
      .note { background: #e7f5ff; border-left: 3px solid #74c0fc; padding: 0.6rem 0.85rem; border-radius: 4px; margin: 1rem 0; color: #1c7ed6; }
    </style>
  </head>
  <body>
    <h1>Bug-Fab <span class="badge">POC</span></h1>
    <p>This page is the public demo for
    <a href="https://github.com/AZgeekster/Bug-Fab">Bug-Fab</a>, a tiny
    framework-agnostic bug-reporting tool for web apps. Click any red
    button below to break something on purpose, then click the bug icon
    bottom-right to file a report. The captured console errors and
    failing network requests get attached to the submission automatically.</p>

    <div class="note">
      Heads up: this is a public sandbox. Any report submitted here is
      visible to anyone who opens
      <a href="/admin/bug-reports/">/admin/bug-reports/</a>. Don't paste
      anything you wouldn't post on Twitter.
    </div>

    <h2>Try a bug</h2>
    <div class="demo-grid">
      <button id="demo-throw">
        Throw uncaught error
        <span class="what">A JS Error bubbles up to window.onerror.</span>
      </button>
      <button id="demo-reference">
        Reference undefined variable
        <span class="what">ReferenceError: nope is not defined.</span>
      </button>
      <button id="demo-typeerror">
        TypeError on null
        <span class="what">Cannot read properties of null (reading 'x').</span>
      </button>
      <button id="demo-console-error">
        console.error log
        <span class="what">Quiet error \u2014 only console.error fires.</span>
      </button>
      <button id="demo-rejection">
        Unhandled promise rejection
        <span class="what">Async failure with no .catch handler.</span>
      </button>
      <button id="demo-fetch-404">
        Failing fetch (404)
        <span class="what">GET /demo/missing returns 404 Not Found.</span>
      </button>
      <button id="demo-fetch-500">
        Failing fetch (500)
        <span class="what">GET /demo/explode crashes server-side.</span>
      </button>
      <button id="demo-fetch-network">
        Network error (bad host)
        <span class="what">DNS failure on a host that does not resolve.</span>
      </button>
    </div>

    <h2>What just happened</h2>
    <div id="demo-log" aria-live="polite"></div>

    <h2>How it works</h2>
    <ol>
      <li>The Bug-Fab bundle on this page hooks <code>window.onerror</code>,
        <code>unhandledrejection</code>, <code>console.error/warn</code>,
        and <code>fetch</code>.</li>
      <li>When you click a red button it trips one of those paths.</li>
      <li>When you click the floating bug icon, the captured events are
        attached to the report's <code>context.console_errors</code> and
        <code>context.network_log</code> fields.</li>
      <li>Submitting saves the report; if a viewer is enabled it shows up
        at <a href="/admin/bug-reports/">/admin/bug-reports/</a>.</li>
    </ol>

    <p>Source: <a href="https://github.com/AZgeekster/Bug-Fab/tree/main/examples/error-playground">examples/error-playground/</a> &middot;
    Roll your own: <a href="https://github.com/AZgeekster/Bug-Fab/blob/main/docs/INSTALLATION.md">INSTALLATION.md</a> &middot;
    Host this image: <a href="https://github.com/AZgeekster/Bug-Fab/blob/main/docs/POC_HOSTING.md">POC_HOSTING.md</a></p>

    <script src="/bug-fab/static/bug-fab.js" defer></script>
    <script>
      window.addEventListener("DOMContentLoaded", () => {
        window.BugFab.init({ submitUrl: "/api/bug-reports" });

        const log = document.getElementById("demo-log");
        const note = (msg) => {
          const ts = new Date().toLocaleTimeString();
          log.textContent = "[" + ts + "] " + msg + "\n" + log.textContent;
        };

        document.getElementById("demo-throw").addEventListener("click", () => {
          note("throwing Error...");
          setTimeout(() => { throw new Error("Demo: synchronous throw at " + new Date().toISOString()); }, 0);
        });

        document.getElementById("demo-reference").addEventListener("click", () => {
          note("referencing undefined variable...");
          setTimeout(() => { nope.fn(); }, 0);
        });

        document.getElementById("demo-typeerror").addEventListener("click", () => {
          note("calling method on null...");
          setTimeout(() => {
            const target = null;
            target.x = 1;
          }, 0);
        });

        document.getElementById("demo-console-error").addEventListener("click", () => {
          note("logging via console.error...");
          console.error("Demo console.error: something looked off in the widget at " + new Date().toISOString());
        });

        document.getElementById("demo-rejection").addEventListener("click", () => {
          note("rejecting a promise...");
          Promise.reject(new Error("Demo: promise rejected at " + new Date().toISOString()));
        });

        document.getElementById("demo-fetch-404").addEventListener("click", async () => {
          note("GET /demo/missing ...");
          try {
            const r = await fetch("/demo/missing");
            note("  -> HTTP " + r.status);
          } catch (err) { note("  -> threw: " + err); }
        });

        document.getElementById("demo-fetch-500").addEventListener("click", async () => {
          note("GET /demo/explode ...");
          try {
            const r = await fetch("/demo/explode");
            note("  -> HTTP " + r.status);
          } catch (err) { note("  -> threw: " + err); }
        });

        document.getElementById("demo-fetch-network").addEventListener("click", async () => {
          note("GET http://this-host-does-not-resolve.invalid/ ...");
          try {
            const r = await fetch("http://this-host-does-not-resolve.invalid/", { mode: "no-cors" });
            note("  -> HTTP " + r.status);
          } catch (err) { note("  -> network error: " + err); }
        });
      });
    </script>
  </body>
</html>
"""


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
