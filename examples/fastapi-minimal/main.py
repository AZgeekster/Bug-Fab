"""Minimum-viable Bug-Fab integration for a FastAPI app.

Run from this directory with ``python main.py`` and open
``http://localhost:8000/`` to exercise the floating-bug-icon flow.
Submitted reports land in ``./bug_reports/`` next to this file; the
admin viewer lives at ``http://localhost:8000/admin/bug-reports``.

The Bug-Fab-specific lines are seven in total: one storage object, one
``configure(...)`` call (the viewer shares the submit router's
dependency-injection state), two ``include_router(...)`` mounts, one
static mount, plus a ``<script>`` tag and an init call in the demo
template. Everything else here is a stock FastAPI app.
"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import bug_fab
from bug_fab.routers import submit, viewer

# WHY a sibling directory: keeps the demo self-contained — wiping
# ./bug_reports/ between runs gives you a fresh slate without touching
# anything else.
STORAGE_DIR = Path(__file__).resolve().parent / "bug_reports"


def _resolve_static_dir() -> Path:
    """Locate the Bug-Fab static bundle on disk.

    Wheel installs ship the bundle at ``<site-packages>/bug_fab/static``
    via the package's ``shared-data`` hook. Editable installs
    (``pip install -e``) leave it one directory up at ``<repo>/static``.
    Probe both so the same example runs in either layout.
    """
    package_root = Path(bug_fab.__file__).resolve().parent
    candidates = [package_root / "static", package_root.parent / "static"]
    for candidate in candidates:
        if (candidate / "bug-fab.js").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not locate the Bug-Fab static bundle. Tried: "
        + ", ".join(str(c) for c in candidates)
    )


def create_app() -> FastAPI:
    """Build the FastAPI app with Bug-Fab wired in.

    Exposed as a factory so test harnesses (and ``uvicorn --factory``)
    can rebuild a fresh instance without re-importing the module.
    """
    app = FastAPI(title="Bug-Fab FastAPI Minimal Example")

    storage = bug_fab.FileStorage(storage_dir=STORAGE_DIR)
    # WHY only one configure() call: the viewer router pulls its
    # storage / settings / github-sync dependencies through the same
    # module-level handles the submit router writes to, so a single
    # configure() wires both routers in one shot.
    submit.configure(storage=storage)

    # WHY no /bug-reports suffix on the submit prefix: the router itself
    # owns the trailing path component (POST /bug-reports), so mounting
    # under /api yields the canonical /api/bug-reports endpoint. Same
    # idea for the viewer: it owns "" + "/{report_id}" + sub-paths.
    app.include_router(submit.submit_router, prefix="/api")
    app.include_router(viewer.viewer_router, prefix="/admin/bug-reports")
    app.mount(
        "/bug-fab/static",
        StaticFiles(directory=str(_resolve_static_dir())),
        name="bug-fab-static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        """Render a tiny demo page that loads and configures the FAB."""
        return DEMO_PAGE

    return app


DEMO_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Bug-Fab FastAPI Minimal Example</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { font-family: system-ui, sans-serif; max-width: 640px; margin: 4rem auto; padding: 0 1rem; color: #212529; }
      code { background: #f1f3f5; padding: 0.1rem 0.35rem; border-radius: 3px; }
      a { color: #1971c2; }
    </style>
  </head>
  <body>
    <h1>MyApp (Bug-Fab demo)</h1>
    <p>This page has nothing to demo on its own — the point is the
    little red bug icon in the bottom-right corner. Click it, draw on
    the screenshot, fill in a title, and submit. The report lands in
    <code>./bug_reports/</code>.</p>
    <p>The admin viewer is at
    <a href="/admin/bug-reports">/admin/bug-reports</a>.</p>
    <script src="/bug-fab/static/bug-fab.js" defer></script>
    <script>
      window.addEventListener("DOMContentLoaded", () => {
        window.BugFab.init({ submitUrl: "/api/bug-reports" });
      });
    </script>
  </body>
</html>
"""


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
