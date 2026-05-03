# Bug-Fab

A drop-in floating action button that lets your users file a bug report
without leaving the page. Screenshot, on-image annotations, console
errors, and recent network calls all ride along automatically.

> **Try it live:** <https://bug-fab.fly.dev/> — click any red button,
> click the bug icon, submit. Reports show up at
> [/admin/bug-reports/](https://bug-fab.fly.dev/admin/bug-reports/).

It's vibe-coded by a hobbyist who happens to use it at the day job too,
so it probably has bugs. Don't bash me.

[![CI](https://img.shields.io/badge/CI-pending-lightgrey)](https://github.com/AZgeekster/Bug-Fab/actions)
[![PyPI](https://img.shields.io/badge/pypi-not%20yet%20published-lightgrey)](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/INSTALLATION.md#pre-release-vs-final)
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
- A live demo so you can click around without installing anything:
  **<https://bug-fab.fly.dev/>**. Eight intentional-error buttons let you
  break things on purpose; the FAB captures it all and submits to a
  shared, public viewer at
  [/admin/bug-reports/](https://bug-fab.fly.dev/admin/bug-reports/).
  Don't paste anything you wouldn't post on Twitter — every report is
  visible to anyone who opens the viewer.

## Quickstart (FastAPI)

Bug-Fab is **not yet on PyPI** — the alpha is gated behind PyPI Trusted
Publishing setup. Until then, install from GitHub pinned to a SHA:

```bash
# Replace <sha> with a 7+ character commit SHA from
# https://github.com/AZgeekster/Bug-Fab/commits/main
pip install "bug-fab @ git+https://github.com/AZgeekster/Bug-Fab.git@<sha>"
```

Once `0.1.0a1` lands on PyPI: `pip install --pre bug-fab` (drop `--pre`
after `0.1.0` final). See [`docs/INSTALLATION.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/INSTALLATION.md#pre-release-vs-final) for the full install matrix.

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
| Write your own backend adapter in Fastify / Express / Razor / SvelteKit / Go | [`docs/ADAPTERS.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS.md) + [`docs/PROTOCOL.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md) |
| Drop Bug-Fab into a Fastify + Next.js + Postgres + PM2 app (full walkthrough) | [`docs/integrations/fastify-nextjs-postgres.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/integrations/fastify-nextjs-postgres.md) |
| See which adapters exist (reference / community-maintained / sketch / wanted) | [`docs/ADAPTERS_REGISTRY.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md) |
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

Pre-release alpha. `0.1.0a1` is built but **not yet on PyPI** — Trusted
Publishing setup is the gating step. Until then, install from GitHub
pinned to a SHA (see [Quickstart](#quickstart-fastapi)). The wire
protocol is not yet locked; the first `0.1.0` final release ships once
the protocol is exercised against a real consumer integration. Don't
pin production work to the alpha — but please kick the tires and file
issues.

## License

[MIT](https://github.com/AZgeekster/Bug-Fab/blob/main/LICENSE).
