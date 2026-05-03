"""Bug-Fab in a templated FastAPI app, ready to ship in a container.

This example mirrors a realistic small private FastAPI app — Jinja2
templates rendering full HTML pages (not a JSON API), SQLite for
queryable metadata, and a Dockerfile + ``docker-compose.yml`` so
``docker compose up`` is the only command between clone and a running
service.

Run locally without Docker:

    pip install -r requirements.txt
    uvicorn main:app --reload

Run in a container:

    docker compose up --build

Either way, open http://localhost:8000/, click the bug icon in the
bottom-right, draw on the screenshot, type a title, submit. Reports
land in the SQLite database at ``./data/bug-fab.db`` (mounted as a
volume in the compose case) and screenshots in ``./data/screenshots/``.
The admin viewer lives at http://localhost:8000/admin/bug-reports.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import bug_fab
from bug_fab.routers import submit, viewer
from bug_fab.storage import SQLiteStorage

# WHY a ``data/`` sibling: keeps DB + screenshots in one place so the
# Docker volume mount and a local-dev ``rm -rf data/`` are both
# one-liners. ``BUG_FAB_DATA_DIR`` overrides this in the container.
DATA_DIR = Path(os.environ.get("BUG_FAB_DATA_DIR", Path(__file__).resolve().parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "bug-fab.db"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _resolve_static_dir() -> Path:
    """Locate the Bug-Fab static bundle on disk.

    Wheel installs ship the bundle at ``<site-packages>/bug_fab/static``;
    editable installs leave it at ``<repo>/static``. Probe both.
    """
    package_root = Path(bug_fab.__file__).resolve().parent
    for candidate in (package_root / "static", package_root.parent / "static"):
        if (candidate / "bug-fab.js").is_file():
            return candidate
    raise FileNotFoundError("Could not locate the Bug-Fab static bundle.")


def create_app() -> FastAPI:
    """Build the FastAPI app with Bug-Fab + Jinja2 wired in."""
    app = FastAPI(title="Bug-Fab Jinja + Docker Example")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # SQLiteStorage gives queryable metadata + indexed list/filter for
    # the viewer. ``__init__`` calls ``create_all()`` so the schema is
    # ready on first use; for production schema migrations between
    # Bug-Fab versions, see DEPLOYMENT_OPTIONS.md § "Upgrading between
    # Bug-Fab versions" for the bundled-Alembic recipe.
    storage = SQLiteStorage(db_path=DB_PATH, screenshot_dir=SCREENSHOT_DIR)
    submit.configure(storage=storage)

    app.include_router(submit.submit_router, prefix="/api")
    app.include_router(viewer.viewer_router, prefix="/admin/bug-reports")
    app.mount(
        "/bug-fab/static",
        StaticFiles(directory=str(_resolve_static_dir())),
        name="bug-fab-static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """Render the home page with the Bug-Fab FAB embedded."""
        return templates.TemplateResponse(
            "home.html",
            {"request": request, "app_name": "MyApp", "page_title": "Welcome"},
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 — container default
