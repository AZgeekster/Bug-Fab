# Bug-Fab

A drop-in floating action button that lets your users file a bug report
without leaving the page. Screenshot, on-image annotations, console
errors, and recent network calls all ride along automatically.

It's vibe-coded by a hobbyist who happens to use it at the day job too,
so it probably has bugs. Don't bash me.

[![CI](https://img.shields.io/badge/CI-pending-lightgrey)](https://github.com/AZgeekster/Bug-Fab/actions)
[![PyPI](https://img.shields.io/pypi/v/bug-fab?label=pypi)](https://pypi.org/project/bug-fab/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/AZgeekster/Bug-Fab/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://github.com/AZgeekster/Bug-Fab)

## What it is

Bug-Fab is a framework-agnostic in-app bug reporter. The end-user clicks
a floating button, the page snapshots itself, the user marks up the
screenshot and types what's wrong, and the package POSTs the whole
bundle to a documented HTTP wire protocol. Your backend stores it
however you like.

The wire protocol is the design center, not the Python adapter. Bug-Fab
v0.1 ships:

- A versioned multipart **HTTP intake spec** — see [`docs/PROTOCOL.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md).
- A **vanilla-JS frontend bundle** (FAB + overlay + annotation canvas +
  console/network buffer + vendored `html2canvas`) that drops into any
  page regardless of backend.
- A **FastAPI reference adapter** with three pluggable storage backends
  (file / SQLite / Postgres) and an optional GitHub Issues sync.
- A **conformance pytest plugin** so adapter authors can verify their
  implementation against the spec.
- A live demo so you can click around without installing anything (URL
  coming soon — POC is being deployed to Fly.io alongside the `0.1.0`
  final release).

## Quickstart (FastAPI)

```bash
pip install --pre bug-fab    # the alpha; drop --pre after v0.1.0 final
```

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from importlib.resources import files
import bug_fab
from bug_fab.routers import submit as submit_module

app = FastAPI()

# Pick a storage backend (file is the default; SQLite/Postgres also ship)
storage = bug_fab.FileStorage(storage_dir="./bug_reports")

# Configure once at startup — viewer reuses the same storage via shared deps
submit_module.configure(storage=storage)

# Mount the routers. submit_router defines POST /bug-reports internally,
# so mount it at the parent prefix (NOT at /api/bug-reports — that double-segments).
app.include_router(bug_fab.submit_router, prefix="/api")
app.include_router(bug_fab.viewer_router, prefix="/admin/bug-reports")

# Serve the frontend bundle. Use importlib.resources so this works in
# both editable installs and wheel installs.
static_dir = str(files("bug_fab").joinpath("static"))
app.mount("/bug-fab/static", StaticFiles(directory=static_dir), name="bug-fab-static")
```

Add one line to your base template:

```html
<script src="/bug-fab/static/bug-fab.js" defer></script>
```

That's it — a FAB now appears on every page, and submitted reports
land in `./bug_reports/`.

## Where to read next

| If you want to... | Read |
|---|---|
| Install Bug-Fab in a FastAPI / Flask / SPA app | [`docs/INSTALLATION.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/INSTALLATION.md) |
| Pick a storage backend, configure auth at the mount point, turn on rate limiting or GitHub sync | [`docs/DEPLOYMENT_OPTIONS.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/DEPLOYMENT_OPTIONS.md) |
| Write your own backend adapter in Razor / Express / SvelteKit / Go | [`docs/ADAPTERS.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS.md) + [`docs/PROTOCOL.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md) |
| Verify your adapter against the wire protocol | [`docs/CONFORMANCE.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/CONFORMANCE.md) |
| Self-host the public POC on Fly.io | [`docs/POC_HOSTING.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/POC_HOSTING.md) |
| See common adoption questions | [`docs/FAQ.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/FAQ.md) |
| See what's planned for v0.2 and beyond | [`docs/ROADMAP.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ROADMAP.md) |
| Use Bug-Fab from an AI assistant (Claude Code, Gemini, ChatGPT) | [`AGENTS.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/AGENTS.md) |
| Report a security issue | [`SECURITY.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/SECURITY.md) |
| Contribute | [`docs/CONTRIBUTING.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/CONTRIBUTING.md) |

## What it isn't

Bug-Fab is for **user-initiated** bug reports from a running web app.
It is **not** an error monitor (Sentry), an analytics product
(Mixpanel / PostHog), a logging backend (Loki / Datadog), or an issue
tracker (Jira / Linear) — it integrates with those rather than
replacing them. See [`docs/ROADMAP.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ROADMAP.md) § "Non-goals"
for the full list.

## Status

Pre-release alpha. The `0.1.0a1` build on PyPI exists to reserve the
name and validate the publish workflow; the wire protocol is not yet
locked. The first `0.1.0` final release ships once the protocol is
exercised against a real consumer integration. Don't pin production
work to the alpha — but please kick the tires and file issues.

## License

[MIT](https://github.com/AZgeekster/Bug-Fab/blob/main/LICENSE).
