# Bug-Fab — FastAPI Minimal Example

A single-file FastAPI app that wires Bug-Fab into "MyApp" with the
smallest reasonable amount of integration code.

## Run it

From this directory:

```bash
pip install -e "../..[sqlite]"
python main.py
```

Then open `http://localhost:8000/` in a browser.

(The `[sqlite]` extra is optional for the file-storage backend used
here, but installing it keeps you ready to swap to SQLite without
re-installing.)

## What you'll see

A nearly-empty page with a red bug icon in the bottom-right corner.

1. Click the bug icon. Bug-Fab captures a screenshot and opens the
   submission overlay.
2. Draw on the screenshot if you want, fill in a title, optionally
   pick a severity, then click **Submit**.
3. Look in `bug_reports/` next to this script. You'll see
   `bug-001.json` (metadata) and `bug-001.png` (the annotated
   screenshot), plus an `index.json` for the viewer.
4. Open `http://localhost:8000/admin/bug-reports` to browse the
   submission in the bundled HTML viewer.

## What it demonstrates

- **Storage configuration** — instantiate a `FileStorage` pointed at a
  local directory.
- **Router wiring** — one `submit.configure(storage=...)` call
  dependency-injects the storage backend into both the intake and
  viewer routers, then `include_router(...)` mounts each at its own
  URL prefix.
- **Static bundle serving** — the example resolves the on-disk path
  to `bug-fab.js` whether Bug-Fab was installed as a wheel or in
  editable mode, then mounts it via `StaticFiles` at
  `/bug-fab/static/`.
- **Frontend init** — a single `<script>` tag plus a one-line
  `BugFab.init({ submitUrl: ... })` call in the demo template wires
  the FAB into the page.
- **Admin viewer** — the bundled HTML viewer at
  `/admin/bug-reports` lists submissions with stat cards and per-row
  actions (status edit, delete, bulk close/archive).

## Going to production

This example deliberately skips everything you'd want in a real
deployment: authentication on the viewer, per-IP rate limiting on
the intake, GitHub Issues sync, switching to a SQL backend, and so
on. See [`docs/DEPLOYMENT_OPTIONS.md`](../../docs/DEPLOYMENT_OPTIONS.md)
for the production checklist.
