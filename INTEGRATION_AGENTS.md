# INTEGRATION_AGENTS.md — For AIs Adding Bug-Fab to a Host App

## Who this file is for

You're an AI coding assistant (Claude Code, Cursor, Aider, ChatGPT,
Gemini Code Assist, etc.) working in a session **rooted in a host
web app**, and the human has asked you to add Bug-Fab to it. This
file is the curated entry point for that integration job — short,
opinionated, and pointed at the load-bearing details.

If you're contributing to Bug-Fab itself (writing adapters, editing
the protocol, adding features), read [`AGENTS.md`](AGENTS.md)
instead. That's the broader doc; this is the narrow one.

> **If you only read one paragraph:** install Bug-Fab from GitHub
> with a pinned commit SHA, instantiate a storage backend, call
> `submit.configure(storage=storage)` once, mount the two routers
> under non-empty URL prefixes, mount the static bundle, drop one
> `<script>` tag into your base template. You're done in ~10 lines.

---

## TL;DR for the integrating session

1. **Install from GitHub, not PyPI.** Bug-Fab is not on PyPI yet.
   Use `pip install "bug-fab @ git+https://github.com/AZgeekster/Bug-Fab.git@<sha>"`
   with a real 7+ character SHA in place of `<sha>`. Don't trust any
   PyPI badge you may see; it's pre-flight, not live.
2. **Pick a storage backend at startup.** `FileStorage(storage_dir=...)`
   for hobby / single-process apps, `SQLiteStorage(db_path=..., screenshot_dir=...)`
   for anything that needs the viewer to scale, or `PostgresStorage(...)`
   if Postgres is already in the stack.
3. **Configure the routers once.** `bug_fab.routers.submit.configure(storage=storage)`
   wires both intake and viewer through the same module-level state.
   Skipping this makes every endpoint return 500.
4. **Mount under non-empty prefixes.** The viewer's HTML root requires
   a non-empty prefix (it serves the list page at the prefix root).
   `app.include_router(viewer_router, prefix="/admin/bug-reports")` is
   the canonical choice. Submit goes under `/api` (not `/api/bug-reports`
   — the router itself owns the trailing path component).
5. **One `<script>` tag in the host template.** Load `bug-fab.js`
   from the static mount, then call `window.BugFab.init({ submitUrl:
   "/api/bug-reports" })` after `DOMContentLoaded`. Done.

---

## Required reading (in order)

1. [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — the binding wire-protocol
   spec. Read this even if you're using the Python adapter — most
   field-name confusion comes from skipping the protocol and lifting
   shapes from internet-aged docs.
2. [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — install paths per
   framework. Has the canonical FastAPI + Flask + Django wiring
   snippets.
3. [`AGENTS.md`](AGENTS.md) — the broader AI-assistant doc covering
   contribution flows + adapter authorship. Skim "The contract —
   load-bearing facts" before you start.
4. **This file.** Short. Stays in your working set.

---

## Common gotchas (real-world findings, all currently fixed)

These are the four bugs a real consumer integration hit on
2026-05-03. They're all addressed in current `main`, but they
illustrate the failure modes you'd repeat if you skip the doc set
above:

- **Don't trust the PyPI badge yet.** `pip install --pre bug-fab`
  fails — Bug-Fab is on GitHub only until the publish workflow
  ships. Use the `git+https://...@<sha>` form. Pin to a real SHA;
  don't leave `<sha>` literal in your `requirements.txt`.
- **Auto-init schema is on by default.** `SqlStorageBase.__init__`
  calls `Base.metadata.create_all(self.engine)` on construction, so
  the viewer no longer 500s on first hit when you skipped a migration
  step. You don't need to run `alembic upgrade head` for fresh
  installs — only for *upgrades* that add columns. See
  [`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) §
  "Upgrading between Bug-Fab versions."
- **`init({ enabled: false })` works.** Pass a literal `false` to
  fully disable the bundle without ripping out the `<script>` tag.
  Useful for per-route opt-out.
- **`SQLiteStorage` arg is `db_path`, not `db_url`.** Older drafts
  and some cached docs say `db_url`. The current parameter is
  `db_path` — pass a filesystem path; the SQLAlchemy URL is built
  internally.

---

## Mount-prefix invariant

This trips up almost every adapter and every integrator. The viewer
serves an HTML list page at the **root of its mount prefix**, which
means the prefix cannot be empty or `/`. Pick `/admin/bug-reports`,
`/internal/feedback`, `/_bugs`, anything but `/`. The bundled
FastAPI router enforces this; community adapters in other languages
should too.

The full 12-point adapter-authorship checklist (the formal version
of this rule) lives at
[`docs/ADAPTERS_REGISTRY.md`](docs/ADAPTERS_REGISTRY.md) §
"Adapter authorship checklist." If you're integrating an adapter
written in something other than Python, walk that checklist before
trusting the adapter's claim of conformance.

---

## Standard wiring snippet (FastAPI)

The full reference lives at
[`examples/fastapi-jinja-docker/main.py`](examples/fastapi-jinja-docker/main.py).
Abbreviated for in-context use:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import bug_fab
from bug_fab.routers import submit, viewer

app = FastAPI()
storage = bug_fab.FileStorage(storage_dir="./bug_reports")
submit.configure(storage=storage)

app.include_router(submit.submit_router, prefix="/api")
app.include_router(viewer.viewer_router, prefix="/admin/bug-reports")

# Static bundle: probe wheel-install path first, fall back to editable.
package_root = Path(bug_fab.__file__).resolve().parent
static_dir = package_root / "static"
if not (static_dir / "bug-fab.js").is_file():
    static_dir = package_root.parent / "static"
app.mount("/bug-fab/static", StaticFiles(directory=str(static_dir)),
          name="bug-fab-static")
```

Then in the host's base template (Jinja2, Razor, ERB, whatever):

```html
<script src="/bug-fab/static/bug-fab.js" defer></script>
<script>
  window.addEventListener("DOMContentLoaded", () => {
    window.BugFab.init({ submitUrl: "/api/bug-reports" });
  });
</script>
```

For Flask, see `bug_fab.adapters.flask.make_blueprint(settings)` —
[`docs/INSTALLATION.md`](docs/INSTALLATION.md) § "Flask consumer"
has the full snippet. For Django, the reusable app docs in the same
file cover `INSTALLED_APPS` + URLconf wiring.

---

## What to ask the user before running edits

Before making any file changes, surface a short scope check. Real
integrations touch ~5 files; the user deserves to confirm before
you start writing:

> I'd like to add Bug-Fab to your app. It will:
>
> - Add `bug-fab[<extra>] @ git+https://github.com/AZgeekster/Bug-Fab.git@<sha>`
>   to your `requirements.txt` (or `pyproject.toml`).
> - Add ~5 lines to your app entry point: instantiate storage, call
>   `submit.configure(storage=...)`, mount the two routers under
>   `/api` and `/admin/bug-reports`, mount the static bundle.
> - Add a `<script>` tag (~6 lines) to your base template so every
>   page renders the bug-report FAB.
> - Optionally add a config flag (env var) to gate the FAB on or off.
>
> Bug-Fab does not phone home, does not run migrations against your
> existing tables, and does not require a new auth system. Reports
> live wherever you point `storage_dir` / `db_path`. OK to proceed?

If the host app uses a non-FastAPI framework (Flask, Django, Razor,
Express, etc.), name the framework explicitly in the scope check
and pull the matching snippet from
[`docs/INSTALLATION.md`](docs/INSTALLATION.md) /
[`docs/ADAPTERS.md`](docs/ADAPTERS.md) before quoting LOC.

---

## When something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Submit endpoint returns 500 with "storage backend is not configured" | `submit.configure(storage=...)` was never called | Add the call before `include_router(...)` |
| Viewer 500s on first GET | Older Bug-Fab without auto-init + missing migration | Upgrade to current `main` (auto-init schema), or run `alembic -c <bundled>/alembic.ini upgrade head` |
| FAB never appears on the page | `<script>` tag missing, or `BugFab.init` called before script loads | Use the `DOMContentLoaded` pattern in the snippet above |
| FAB appears but submit returns 404 | Submit router mounted at the wrong prefix | The router owns `/bug-reports`; mount under `/api`, not `/api/bug-reports` |
| Submit returns 415 | Frontend posted JPEG | The bundled `html2canvas` only emits PNG; this means a custom integrator built their own client. v0.1 protocol is PNG-only. |
| Viewer 404s on every URL | Viewer mounted under `/` or `""` | Mount-prefix invariant: pick a non-empty prefix |

---

## Pointer table

| What you need | Where |
|---|---|
| Wire-protocol spec | [`docs/PROTOCOL.md`](docs/PROTOCOL.md) |
| Per-framework install paths | [`docs/INSTALLATION.md`](docs/INSTALLATION.md) |
| Auth recipes (HTTP Basic / cookie / OAuth2) | [`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) § "Auth recipes" |
| Migrating across Bug-Fab versions | [`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) § "Upgrading between Bug-Fab versions" |
| Adapter-authorship checklist | [`docs/ADAPTERS_REGISTRY.md`](docs/ADAPTERS_REGISTRY.md) |
| Broader AI-assistant doc | [`AGENTS.md`](AGENTS.md) |
| FastAPI minimal example | [`examples/fastapi-minimal/main.py`](examples/fastapi-minimal/main.py) |
| FastAPI + Jinja2 + Docker example | [`examples/fastapi-jinja-docker/main.py`](examples/fastapi-jinja-docker/main.py) |
| Flask minimal example | [`examples/flask-minimal/main.py`](examples/flask-minimal/main.py) |
| Django minimal example | [`examples/django-minimal/`](examples/django-minimal/) |
| Live POC to play with | <https://bug-fab.fly.dev/> |
