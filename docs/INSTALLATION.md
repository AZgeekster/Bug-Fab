# Installation

Bug-Fab ships as a single Python package on PyPI. The package contains
the FastAPI reference adapter, the Pydantic schemas, three storage
backends behind a single `Storage` ABC, and the vanilla-JS frontend
bundle (with `html2canvas` vendored at a pinned version — no CDN).

This guide covers four Python install paths:

1. [FastAPI consumer](#fastapi-consumer) — the canonical path.
2. [Flask consumer](#flask-consumer) — first-party Blueprint shim
   (`bug-fab[flask]`).
3. [Django consumer](#django-consumer) — first-party reusable Django
   app (`bug-fab[django]`).
4. [React / SPA consumer](#react--spa-consumer) — the JS bundle plus a
   protocol-honoring backend (any language).

For non-Python integrations (Fastify, Express, Next.js Route Handlers,
etc.), the Python `bug-fab` package is **not required at runtime** —
you implement the [wire protocol](PROTOCOL.md) directly. Bug-Fab still
ships the conformance suite as a Python pytest plugin, so you may want
`pip install bug-fab` on a CI machine for protocol verification.

- **Fastify + Next.js + PostgreSQL + PM2** — see the full step-by-step
  walkthrough at [`docs/integrations/fastify-nextjs-postgres.md`](./integrations/fastify-nextjs-postgres.md).
- **Other stacks** — see [`docs/ADAPTERS.md`](./ADAPTERS.md) for code-level
  sketches and [`docs/ADAPTERS_REGISTRY.md`](./ADAPTERS_REGISTRY.md) for
  the priority-tiered list of stacks with status (reference / community /
  sketch / wanted).

## Requirements

- Python **3.10**, **3.11**, or **3.12**.
- A modern browser. Internet Explorer is not supported. The frontend
  uses `fetch`, `Promise`, and standard ES2020 features.

## Pre-release vs final

Until `0.1.0` final ships, only the alpha is on PyPI:

```bash
pip install --pre bug-fab    # installs 0.1.0a1
```

Once `0.1.0` final lands, the `--pre` flag goes away:

```bash
pip install bug-fab          # post-0.1.0
```

`pip install bug-fab` without `--pre` skips the alpha by design — that
prevents accidental pinning to a pre-release while the wire protocol is
still being validated against real consumer integrations.

## FastAPI consumer

The FastAPI adapter is the reference implementation. It exposes two
routers (intake + viewer) and a single configurable `Storage` instance.

### 1. Install

```bash
# Default: file-based storage, zero external deps
pip install --pre bug-fab

# Or with SQLAlchemy + SQLite
pip install --pre "bug-fab[sqlite]"

# Or with SQLAlchemy + Postgres (psycopg)
pip install --pre "bug-fab[postgres]"
```

### 2. Wire it into your app

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from importlib.resources import files
import bug_fab
from bug_fab.routers import submit as submit_module

app = FastAPI()

# Pick a storage backend
storage = bug_fab.FileStorage(storage_dir="./bug_reports")
# storage = bug_fab.SQLiteStorage(...)   # requires bug-fab[sqlite]
# storage = bug_fab.PostgresStorage(...) # requires bug-fab[postgres]

# Configure once at startup. The viewer reuses the same storage via
# shared dependency providers from bug_fab.routers.submit, so there's
# no separate viewer.configure() call.
submit_module.configure(storage=storage)

# Mount the routers. submit_router defines POST /bug-reports internally,
# so mount it at the PARENT prefix (`/api`) — mounting at `/api/bug-reports`
# would double-segment the URL to `/api/bug-reports/bug-reports`.
app.include_router(bug_fab.submit_router, prefix="/api")
app.include_router(bug_fab.viewer_router, prefix="/admin/bug-reports")

# Serve the static bundle (FAB + overlay JS/CSS). Using importlib.resources
# resolves the static dir correctly in BOTH editable installs (`<repo>/static/`)
# and wheel installs (`<site-packages>/bug_fab/static/`). The `packages=`
# shortcut for StaticFiles only works in wheel installs.
static_dir = str(files("bug_fab").joinpath("static"))
app.mount(
    "/bug-fab/static",
    StaticFiles(directory=static_dir),
    name="bug-fab-static",
)
```

### 3. Add the FAB to your pages

In your base template (Jinja, HTMX, raw HTML — anything):

```html
<script src="/bug-fab/static/bug-fab.js" defer></script>
<script>
  // The bundle auto-inits on DOMContentLoaded with sensible defaults.
  // To override config, disable auto-init and call init() yourself:
  window.BugFabAutoInit = false;
  window.addEventListener("DOMContentLoaded", () => {
    window.BugFab.init({
      submitUrl: "/api/bug-reports",
      // headers: () => ({ "X-CSRF-Token": getCsrfToken() }),
      // environment: "prod",
      // appVersion: "1.2.3",
    });
  });
</script>
```

That's the whole integration. Visit any page in your app, click the
floating bug icon, and you're submitting reports.

### 4. Configure via env vars (optional)

All knobs have `BUG_FAB_*` env vars; the alternative is to build a
`Settings` object explicitly.

```bash
BUG_FAB_STORAGE_DIR=/var/bug-fab/reports
BUG_FAB_RATE_LIMIT_ENABLED=true
BUG_FAB_RATE_LIMIT_MAX=50
BUG_FAB_VIEWER_PAGE_SIZE=20
BUG_FAB_GITHUB_ENABLED=false
```

```python
from bug_fab.config import Settings
settings = Settings.from_env()      # env vars
settings = Settings.from_env(rate_limit_max=200)  # override at the call site
```

See [DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md) for the full env-var
reference and recommended defaults per deployment shape.

## Flask consumer

Bug-Fab ships a first-party Flask Blueprint shim. A Flask consumer's
integration code is ~10 LOC — the Blueprint exposes all 8 wire-protocol
endpoints, the HTML viewer, the status workflow, bulk operations, and
the static bundle. Validation flows through the same
`bug_fab.intake.validate_payload` the FastAPI router uses, so the wire
contract is shared by construction.

### 1. Install

```bash
pip install --pre 'bug-fab[flask]'
```

The `[flask]` extra adds Flask as a dependency. The Pydantic schemas,
storage backends, and static bundle ship in the main package.

### 2. Wire the Blueprint

```python
from flask import Flask
from bug_fab.adapters.flask import make_blueprint
from bug_fab.config import Settings

settings = Settings(storage_dir="./bug_reports")
app = Flask(__name__)
app.register_blueprint(make_blueprint(settings), url_prefix="/bug-fab")
```

That's it. `GET /bug-fab/` renders the HTML viewer index; `POST
/bug-fab/bug-reports` accepts intake; the static bundle lives at
`GET /bug-fab/static/bug-fab.js`. The mount prefix MUST be non-empty —
the Blueprint's HTML list page lives at the prefix root.

GitHub Issues sync, rate limiting, and CSP-nonce support are wired
through the same `Settings` knobs the FastAPI router uses
(`github_enabled`, `rate_limit_enabled`, `csp_nonce_provider`).

### 3. Add the FAB to your templates

```html
<script src="/bug-fab/static/bug-fab.js" defer></script>
```

See `examples/flask-minimal/` for a complete reference consumer.

## Django consumer

Bug-Fab ships a first-party Django reusable app. Add it to
`INSTALLED_APPS`, run `manage.py migrate`, and mount the URLconfs.
Native Django ORM models, a free `BugReportAdmin` for the admin UI,
plain Django views (no DRF dependency), and a `LoginRequiredMixin`-based
auth helper.

### 1. Install

```bash
pip install --pre 'bug-fab[django]'
```

The `[django]` extra adds Django ≥ 4.2 as a dependency.

### 2. Register the app + run migrations

```python
# settings.py
INSTALLED_APPS = [
    # ...your apps...
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bug_fab.adapters.django",
]

# Django default DATA_UPLOAD_MAX_MEMORY_SIZE is 2.5 MiB which would
# silently truncate Bug-Fab's 10 MiB cap. Bump it explicitly.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024  # 10 MiB + multipart overhead
MEDIA_ROOT = BASE_DIR / "media"
```

```bash
python manage.py migrate
```

### 3. Mount the URLconfs

The intake and viewer URLconfs are split so consumers can guard each
under different middleware (e.g., open intake, auth-required viewer):

```python
# project/urls.py
from django.urls import include, path

urlpatterns = [
    path("api/",                include("bug_fab.adapters.django.urls_intake")),
    path("admin/bug-reports/",  include("bug_fab.adapters.django.urls_viewer")),
    # ...your project's urls...
]
```

The viewer prefix MUST be non-empty; the HTML list page renders at the
prefix root.

### 4. Add the FAB to your templates

The package's `bundle_view` streams the canonical `bug-fab.js` from
`<site-packages>/bug_fab/static/bug-fab.js` so consumers don't need to
copy it into their `staticfiles` tree:

```html
{% load static %}
<script src="{% url 'bug_fab:bundle' %}" defer></script>
```

In production, prefer serving the bundle from your own CDN /
collected-static directory instead of through `bundle_view`. See
`examples/django-minimal/` for a complete reference consumer.

## React / SPA consumer

If your frontend is a build-pipeline SPA (React, SvelteKit, Vue, etc.)
your backend is probably written in something other than Python. The
JS bundle is framework-agnostic — install it as a static asset and
point it at any backend that honors the wire protocol.

### 1. Get the bundle

For v0.1 the JS bundle ships **inside** the Python package; an npm
publish lands in v0.2. The two practical paths today:

- **Backend is Python:** `pip install --pre bug-fab` and serve the
  bundle from `bug_fab/static/`.
- **Backend is anything else:** download `bug-fab-0.1.0.js` from the
  GitHub Release page and serve it as a static asset from your CDN /
  origin / build output.

### 2. Drop in a `<script>` tag

```html
<script src="/static/bug-fab.js" defer></script>
<script>
  window.BugFabAutoInit = false;
  window.addEventListener("DOMContentLoaded", () => {
    window.BugFab.init({
      submitUrl: "https://api.example.com/bug-reports",
    });
  });
</script>
```

The bundle injects the FAB on `DOMContentLoaded`. There is no React
component wrapper required — the overlay renders into a top-level
container with isolated styles.

A reference React component wrapper ships in
[`examples/react-spa/`](../examples/react-spa) for SPA developers who
prefer to import-and-mount rather than script-tag.

### 3. Implement the protocol on your backend

Any language that can accept a `multipart/form-data` POST and respond
with the documented JSON shape will work. See [`PROTOCOL.md`](PROTOCOL.md)
for the wire spec and [`ADAPTERS.md`](ADAPTERS.md) for sketches in
Razor Pages, Express, SvelteKit, and Go.

## Verifying the install

After wiring it up, exercise the round-trip:

1. Open any page in your app.
2. Click the floating bug icon (default position: bottom-right).
3. Type a title and description. Annotate the screenshot if you like.
4. Click **Submit**.
5. Confirm the report shows up:
   - **File backend:** a new directory under `BUG_FAB_STORAGE_DIR`
     containing `metadata.json` and `screenshot.png`.
   - **SQL backends:** a new row in the `bug_reports` table plus a
     screenshot file on disk at the configured path.
   - **Viewer enabled?** The list view at your viewer mount-point now
     shows the new report.

## Next steps

- [DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md) — pick the right
  storage backend, set up auth, configure rate limiting and GitHub
  sync.
- [POC_HOSTING.md](POC_HOSTING.md) — deploy a public demo on Fly.io.
- [FAQ.md](FAQ.md) — common adoption questions and gotchas.
