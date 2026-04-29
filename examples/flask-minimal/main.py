"""Minimum-viable Bug-Fab integration for a Flask app.

Demonstrates that the Bug-Fab wire protocol — not the FastAPI adapter —
is what binds the frontend to a backend. Flask consumers can implement
the protocol directly using ``bug_fab.FileStorage`` plus the Pydantic
schemas, with no FastAPI runtime requirement.

Run from this directory with ``python main.py`` and open
``http://localhost:8000/`` to exercise the floating-bug-icon flow.
Submitted reports land in ``./bug_reports/`` next to this file; the
admin viewer lives at ``http://localhost:8000/admin/bug-reports``.

Scope: implements ``POST /api/bug-reports`` (intake) plus the read-only
viewer routes (list, detail, screenshot). The full status workflow,
bulk operations, and rate limiting are documented in
``docs/ADAPTERS.md`` for self-implementation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    jsonify,
    render_template_string,
    request,
    send_file,
    send_from_directory,
)
from pydantic import ValidationError

import bug_fab
from bug_fab import BugReportCreate, FileStorage

# WHY a sibling directory: keeps the demo self-contained — wiping
# ./bug_reports/ between runs gives you a fresh slate without touching
# anything else.
STORAGE_DIR = Path(__file__).resolve().parent / "bug_reports"

# WHY 10 MiB cap: matches the protocol's documented screenshot ceiling
# (see docs/PROTOCOL.md — Size limits). Adapters MAY enforce stricter.
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"


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


def _error(code: str, detail: str | list[Any], http_status: int) -> Any:
    """Return the protocol-standard error envelope as a Flask response."""
    return jsonify({"error": code, "detail": detail}), http_status


def _detect_image_kind(payload: bytes) -> str | None:
    """Return ``"png"`` / ``"jpeg"`` for known signatures, else ``None``."""
    if payload.startswith(_PNG_SIGNATURE):
        return "png"
    if payload.startswith(_JPEG_SIGNATURE):
        return "jpeg"
    return None


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 — server clock is authoritative."""
    return datetime.now(UTC).isoformat()


def create_app() -> Flask:
    """Build the Flask app with Bug-Fab wired in.

    Exposed as a factory so test harnesses can rebuild a fresh instance
    without re-importing the module.
    """
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = (
        11 * 1024 * 1024
    )  # screenshot + metadata + multipart overhead
    storage = FileStorage(storage_dir=STORAGE_DIR)
    static_dir = _resolve_static_dir()

    @app.post("/api/bug-reports")
    def submit_bug_report() -> Any:
        """Persist a new bug report per the Bug-Fab v0.1 wire protocol."""
        metadata_raw = request.form.get("metadata")
        screenshot_file = request.files.get("screenshot")
        if not metadata_raw or screenshot_file is None:
            return _error("validation_error", "metadata and screenshot are both required", 400)

        # Distinguish "not parseable" from "parseable but invalid" so
        # consumers can branch on the failure mode.
        try:
            metadata_obj: dict[str, Any] = json.loads(metadata_raw)
        except json.JSONDecodeError as exc:
            return _error("validation_error", f"metadata is not valid JSON: {exc.msg}", 400)
        try:
            payload = BugReportCreate.model_validate(metadata_obj)
        except ValidationError as exc:
            return _error("schema_error", exc.errors(), 422)

        screenshot_bytes = screenshot_file.read()
        if not screenshot_bytes:
            return _error("validation_error", "Screenshot file is empty", 400)
        if len(screenshot_bytes) > MAX_SCREENSHOT_BYTES:
            return jsonify(
                {
                    "error": "payload_too_large",
                    "detail": f"Screenshot exceeds maximum size of {MAX_SCREENSHOT_BYTES} bytes",
                    "limit_bytes": MAX_SCREENSHOT_BYTES,
                }
            ), 413
        if _detect_image_kind(screenshot_bytes) is None:
            return _error("unsupported_media_type", "Screenshot must be a PNG or JPEG image", 415)

        # Server-captured User-Agent is the source of truth — preserve
        # the client-supplied value separately for diagnostics.
        server_user_agent = request.headers.get("User-Agent", "")
        client_user_agent = payload.context.user_agent
        environment = payload.context.environment or metadata_obj.get("environment") or ""
        metadata_dict = payload.model_dump(mode="json")
        metadata_dict["server_user_agent"] = server_user_agent
        metadata_dict["client_reported_user_agent"] = client_user_agent
        metadata_dict["environment"] = environment

        # asyncio.run wraps the async storage call so Flask's sync
        # handler can stay sync. Acceptable for the demo's small N; a
        # production Flask consumer might switch to a sync storage shim
        # or run uvicorn/hypercorn with an ASGI Flask wrapper.
        report_id = asyncio.run(storage.save_report(metadata_dict, screenshot_bytes))
        detail = asyncio.run(storage.get_report(report_id))
        if detail is None:
            return _error("internal_error", "Stored report could not be read back", 500)

        return jsonify(
            {
                "id": report_id,
                "received_at": _now_iso(),
                "stored_at": f"file://{STORAGE_DIR.as_posix()}/{report_id}/",
                "github_issue_url": detail.github_issue_url,
            }
        ), 201

    @app.get("/admin/bug-reports")
    def list_bug_reports() -> Any:
        """Render a minimal HTML table of stored reports."""
        items, total = asyncio.run(storage.list_reports({}, page=1, page_size=200))
        return render_template_string(_LIST_PAGE, items=items, total=total)

    @app.get("/admin/bug-reports/<report_id>")
    def report_detail(report_id: str) -> Any:
        """Render the detail view for one report."""
        report = asyncio.run(storage.get_report(report_id))
        if report is None:
            abort(404)
        return render_template_string(_DETAIL_PAGE, report=report)

    @app.get("/admin/bug-reports/<report_id>/screenshot")
    def report_screenshot(report_id: str) -> Any:
        """Serve the raw PNG screenshot for a report."""
        path = asyncio.run(storage.get_screenshot_path(report_id))
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.get("/bug-fab/static/<path:filename>")
    def bug_fab_static(filename: str) -> Any:
        """Serve the vendored Bug-Fab static bundle (JS / CSS / html2canvas)."""
        return send_from_directory(str(static_dir), filename)

    @app.get("/")
    def home() -> str:
        """Render a tiny demo page that loads and configures the FAB."""
        return DEMO_PAGE

    return app


DEMO_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Bug-Fab Flask Minimal Example</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { font-family: system-ui, sans-serif; max-width: 640px; margin: 4rem auto; padding: 0 1rem; color: #212529; }
      code { background: #f1f3f5; padding: 0.1rem 0.35rem; border-radius: 3px; }
      a { color: #1971c2; }
    </style>
  </head>
  <body>
    <h1>MyApp (Bug-Fab Flask demo)</h1>
    <p>This page has nothing to demo on its own &mdash; the point is the
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

_LIST_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Bug-Fab Reports</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #212529; }
      table { width: 100%; border-collapse: collapse; }
      th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #dee2e6; }
      a { color: #1971c2; text-decoration: none; }
      a:hover { text-decoration: underline; }
    </style>
  </head>
  <body>
    <h1>Bug Reports ({{ total }})</h1>
    <p><a href="/">&larr; Back to demo page</a></p>
    {% if items %}
    <table>
      <thead><tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead>
      <tbody>
      {% for item in items %}
        <tr>
          <td><a href="/admin/bug-reports/{{ item.id }}">{{ item.id }}</a></td>
          <td>{{ item.title }}</td>
          <td>{{ item.severity }}</td>
          <td>{{ item.status }}</td>
          <td>{{ item.created_at }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p>No reports yet. Submit one from the demo page.</p>
    {% endif %}
  </body>
</html>
"""

_DETAIL_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{{ report.id }} &mdash; {{ report.title }}</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #212529; }
      dt { font-weight: 600; margin-top: 0.5rem; }
      dd { margin: 0 0 0.5rem 0; }
      img { max-width: 100%; border: 1px solid #dee2e6; }
      pre { background: #f1f3f5; padding: 0.75rem; overflow-x: auto; }
      a { color: #1971c2; }
    </style>
  </head>
  <body>
    <p><a href="/admin/bug-reports">&larr; All reports</a></p>
    <h1>{{ report.id }} &mdash; {{ report.title }}</h1>
    <dl>
      <dt>Severity</dt><dd>{{ report.severity }}</dd>
      <dt>Status</dt><dd>{{ report.status }}</dd>
      <dt>Created</dt><dd>{{ report.created_at }}</dd>
      <dt>Description</dt><dd>{{ report.description or "(none)" }}</dd>
      <dt>Page URL</dt><dd>{{ report.context.url or "(none)" }}</dd>
      <dt>Server User-Agent</dt><dd>{{ report.server_user_agent or "(none)" }}</dd>
    </dl>
    <h2>Screenshot</h2>
    <img src="/admin/bug-reports/{{ report.id }}/screenshot" alt="Screenshot for {{ report.id }}" />
    <h2>Lifecycle</h2>
    <pre>{% for event in report.lifecycle %}{{ event.at }} &mdash; {{ event.action }}{% if event.by %} by {{ event.by }}{% endif %}
{% endfor %}</pre>
  </body>
</html>
"""


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
