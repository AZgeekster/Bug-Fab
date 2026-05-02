"""Flask adapter for Bug-Fab — install via ``pip install bug-fab[flask]``.

Public surface is :func:`make_blueprint`. A typical Flask consumer wires
Bug-Fab in three lines::

    from flask import Flask
    from bug_fab.adapters.flask import make_blueprint
    from bug_fab.config import Settings

    app = Flask(__name__)
    app.register_blueprint(
        make_blueprint(Settings(storage_dir="bug_reports")),
        url_prefix="/bug-fab",
    )

The blueprint provides every endpoint defined in
``docs/PROTOCOL.md`` § Endpoints (intake, list, detail, screenshot,
status update, delete, bulk-close, bulk-archive), the HTML viewer
list/detail pages, and the static-bundle serve at
``<prefix>/static/bug-fab.js``.
"""

from __future__ import annotations

from bug_fab.adapters.flask.blueprint import make_blueprint

__all__ = ["make_blueprint"]
