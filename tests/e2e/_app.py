"""Minimal FastAPI harness used by the e2e smoke test.

Differs from ``examples/fastapi-minimal/main.py`` in two ways:

* honors ``BUG_FAB_E2E_STORAGE_DIR`` so each test run gets isolated storage
  without polluting any source tree;
* serves a tiny ``/`` page wired to the FAB so Playwright has something to
  click without depending on the example's exact markup.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import bug_fab
from bug_fab.routers import submit, viewer


def _resolve_static_dir() -> Path:
    package_root = Path(bug_fab.__file__).resolve().parent
    for candidate in (package_root / "static", package_root.parent / "static"):
        if (candidate / "bug-fab.js").is_file():
            return candidate
    raise FileNotFoundError("bug-fab.js bundle not found near " + str(package_root))


def create_app() -> FastAPI:
    storage_dir = Path(os.environ["BUG_FAB_E2E_STORAGE_DIR"])
    storage_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="bug-fab e2e harness")
    storage = bug_fab.FileStorage(storage_dir=storage_dir)
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
        return _PAGE

    return app


_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>bug-fab e2e</title>
  </head>
  <body>
    <h1>e2e harness</h1>
    <p>Driven by Playwright.</p>
    <script src="/bug-fab/static/bug-fab.js" defer></script>
    <script>
      window.addEventListener("DOMContentLoaded", function () {
        window.BugFab.init({ submitUrl: "/api/bug-reports" });
      });
    </script>
  </body>
</html>
"""


app = create_app()
