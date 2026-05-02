# Flask minimal example

A single-file Flask app that integrates Bug-Fab via the first-party
`bug_fab.adapters.flask` Blueprint. Total integration code: roughly 15
lines once you peel off the demo page boilerplate.

## What this demonstrates

- The `bug_fab.adapters.flask.make_blueprint(settings)` factory drops
  the entire Bug-Fab wire protocol onto a Flask app under one URL
  prefix &mdash; intake, JSON viewer, HTML viewer, status workflow,
  bulk operations, and the static bundle.
- The Bug-Fab frontend bundle (`bug-fab.js`) submits to the Flask
  blueprint unmodified. The protocol &mdash; not the FastAPI adapter
  &mdash; is the contract.
- A Flask consumer pays no FastAPI runtime cost. The blueprint depends
  only on Flask &geq; 3 plus Bug-Fab core.

## Run it

```bash
cd examples/flask-minimal
pip install -e "../..[flask]"   # bug-fab + Flask adapter from the local checkout
python main.py
```

Then open <http://localhost:8000/> and click the floating bug icon in
the bottom-right corner. Submitted reports land in `./bug_reports/`
next to `main.py`; browse them at <http://localhost:8000/bug-fab/>.

## File layout

```
flask-minimal/
├── README.md           this file
├── main.py             single-file Flask app (demo page + blueprint mount)
├── requirements.txt    pip-installable runtime deps
├── .gitignore          excludes the local bug_reports/ directory
└── bug_reports/        (created at first submit; gitignored)
```

## How the integration works

```python
from flask import Flask
from bug_fab.adapters.flask import make_blueprint
from bug_fab.config import Settings

app = Flask(__name__)
app.register_blueprint(
    make_blueprint(Settings(storage_dir="bug_reports")),
    url_prefix="/bug-fab",
)
```

That's the whole thing. The blueprint:

| Path | Method | Purpose |
|------|--------|---------|
| `/bug-fab/bug-reports` | POST | Submit a new report (intake) |
| `/bug-fab/` | GET | HTML viewer list page |
| `/bug-fab/<id>` | GET | HTML viewer detail page |
| `/bug-fab/reports` | GET | JSON list (filterable) |
| `/bug-fab/reports/<id>` | GET | JSON detail |
| `/bug-fab/reports/<id>/screenshot` | GET | Raw PNG bytes |
| `/bug-fab/reports/<id>/status` | PUT | Lifecycle status update |
| `/bug-fab/reports/<id>` | DELETE | Hard delete |
| `/bug-fab/bulk-close-fixed` | POST | Bulk close all `fixed` reports |
| `/bug-fab/bulk-archive-closed` | POST | Bulk archive all `closed` reports |
| `/bug-fab/static/<path>` | GET | Vendored frontend bundle |

## Notes for production Flask consumers

- **Mount-prefix is required.** The viewer's HTML list page lives at
  the blueprint's root path (`GET ""`); mounting at the host app's
  root would clash with the consumer's own routes.
- **Async bridge:** Bug-Fab's `Storage` ABC is async; the adapter wraps
  every call in `asyncio.run` per request. Acceptable for v0.1; a
  consumer measuring real load can either run a long-lived loop in a
  worker thread or switch to a sync storage shim.
- **Auth is mount-point delegation.** Protect the prefix behind your
  existing Flask-Login / Flask-Security middleware. Bug-Fab v0.1 ships
  no auth abstraction (see `docs/PROTOCOL.md` &sect; Auth).
- **Per-route gates:** the same `viewer_permissions` flags the FastAPI
  reference uses (`can_edit_status`, `can_delete`, `can_bulk`) gate
  status updates, deletes, and bulk operations on the blueprint.
- **MAX_CONTENT_LENGTH:** set to 11 MiB to match the protocol's
  recommended total-request cap. Flask returns `413` itself for
  over-cap requests before any handler runs.

## Custom storage backends

`make_blueprint(settings, storage=...)` accepts an explicit `Storage`
instance. Use it to plug `SQLiteStorage`, `PostgresStorage`, or a
contrib backend without bringing those optional dependencies into the
default Flask install:

```python
from bug_fab.storage.sqlite import SQLiteStorage

storage = SQLiteStorage(db_path="bug_reports.db", screenshot_dir="bug_reports")
storage.create_all()
app.register_blueprint(
    make_blueprint(settings, storage=storage),
    url_prefix="/bug-fab",
)
```
