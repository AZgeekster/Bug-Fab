"""Minimum-viable Bug-Fab integration for a Flask app.

Demonstrates the v0.1 first-party Flask adapter:
``bug_fab.adapters.flask.make_blueprint`` returns a single Flask
``Blueprint`` exposing the entire Bug-Fab wire protocol — intake, JSON
viewer, HTML viewer, status updates, bulk operations, and the static
bundle. Drop the blueprint onto any Flask app under a non-empty URL
prefix and the floating bug icon submits to it.

Run from this directory with ``python main.py`` and open
``http://localhost:8000/`` to exercise the floating-bug-icon flow.
Submitted reports land in ``./bug_reports/`` next to this file; the
viewer lives at ``http://localhost:8000/bug-fab/``.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from bug_fab.adapters.flask import make_blueprint
from bug_fab.config import Settings

# Reports persist to a sibling directory so wiping ``./bug_reports/``
# between runs gives a fresh slate without touching anything else.
STORAGE_DIR = Path(__file__).resolve().parent / "bug_reports"

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
    <p>Click the red bug icon in the bottom-right corner, draw on the
    screenshot, fill in a title, and submit. Reports land in
    <code>./bug_reports/</code> and appear at
    <a href="/bug-fab/">/bug-fab/</a>.</p>
    <script src="/bug-fab/static/bug-fab.js" defer></script>
    <script>
      window.addEventListener("DOMContentLoaded", () => {
        window.BugFab.init({ submitUrl: "/bug-fab/bug-reports" });
      });
    </script>
  </body>
</html>
"""


def create_app() -> Flask:
    """Build the Flask app with Bug-Fab wired in.

    Exposed as a factory so test harnesses can rebuild a fresh instance
    without re-importing the module.
    """
    app = Flask(__name__)
    # 11 MiB matches the protocol's recommended total-request cap
    # (10 MiB screenshot + metadata JSON + multipart overhead). Flask
    # returns 413 itself for over-cap requests before any handler runs.
    app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024

    settings = Settings(storage_dir=STORAGE_DIR)
    app.register_blueprint(make_blueprint(settings), url_prefix="/bug-fab")

    @app.get("/")
    def home() -> str:
        return DEMO_PAGE

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
