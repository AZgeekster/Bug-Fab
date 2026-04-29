# Deployment Options

How to configure Bug-Fab for a real deployment. The defaults work for
most consumers; this doc covers the knobs you'll want to think about
when standing up a production-grade instance.

Topics:

- [Storage backend choice](#storage-backend-choice)
- [Router mount-point auth pattern](#router-mount-point-auth-pattern)
- [Viewer permissions config](#viewer-permissions-config)
- [Rate limiting](#rate-limiting)
- [GitHub Issues sync](#github-issues-sync)
- [html2canvas vendoring](#html2canvas-vendoring)
- [Remote-collector pattern](#remote-collector-pattern)
- [Cross-origin intake (CORS)](#cross-origin-intake-cors)
- [Content Security Policy (CSP)](#content-security-policy-csp)

## Storage backend choice

v0.1 ships three storage backends behind a single `Storage` ABC:
`FileStorage` (default), `SQLiteStorage`, and `PostgresStorage`.
**Screenshots always live on disk** regardless of the metadata
backend — never blob-in-DB. That keeps row sizes small, makes
screenshots servable as static files, and gives you an easy escape
hatch for forensic inspection.

| Backend | Extra deps | When to pick it |
|---|---|---|
| `FileStorage` | None — pure stdlib | Hobby projects, single-process apps, anywhere you'd rather not run a database. Reports land as one directory per ID containing `metadata.json` + `screenshot.png`. Backup = `tar` the directory. |
| `SQLiteStorage` | `bug-fab[sqlite]` (SQLAlchemy + Alembic) | You want list/filter/search performance but not a database server. The single SQLite file plus the on-disk screenshot directory is the whole storage layer. Excellent for self-hosted apps with one box. |
| `PostgresStorage` | `bug-fab[postgres]` (SQLAlchemy + Alembic + psycopg) | You're already running Postgres for the rest of the app. Reports become a real table you can join, index, and query alongside everything else. Screenshots still on disk — back up the directory and the database. |

### Tradeoffs at a glance

- **Query power**: file < SQLite ≈ Postgres. The viewer list/filter
  endpoints work on all three, but only the SQL backends scale to
  tens of thousands of reports without table scans.
- **Backup story**: file = `tar` one folder; SQL = back up the database
  *and* the screenshot directory (two artifacts).
- **Migration path**: switching between backends later is supported but
  manual in v0.1 — the schema is identical. A first-class
  export/import script lands in v0.2.
- **Container ephemerals**: in container deployments, mount
  `BUG_FAB_STORAGE_DIR` to a persistent volume regardless of metadata
  backend, otherwise screenshots disappear on redeploy.

### Configuring storage

```python
from bug_fab import FileStorage
storage = FileStorage(storage_dir="/var/bug-fab/reports")

# from bug_fab.storage import SQLiteStorage
# storage = SQLiteStorage(
#     db_url="sqlite:///./bug-fab.db",
#     screenshot_dir="/var/bug-fab/screenshots",
# )

# from bug_fab.storage import PostgresStorage
# storage = PostgresStorage(
#     db_url="postgresql+psycopg://user:pw@host/dbname",
#     screenshot_dir="/var/bug-fab/screenshots",
# )
```

Or set `BUG_FAB_STORAGE_DIR` in the environment and use
`Settings.from_env()`. See [INSTALLATION.md](INSTALLATION.md) for the
env-var list.

## Router mount-point auth pattern

Bug-Fab v0.1 ships **no auth abstraction**. A proper `AuthAdapter` ABC
lands in v0.2 once real consumer integrations show what methods are
actually needed (`is_admin`, `audit_view`, etc.).

In v0.1, you protect routes by mounting the two routers (`submit_router`
and `viewer_router`) under URL prefixes that your existing auth
middleware already covers. The patterns below cover the three common
shapes.

> **Setup note (applies to every pattern below):** the routers are module-level `APIRouter` instances, not factories. Configure storage once at startup via `bug_fab.routers.submit.configure(storage=storage)` — the viewer reuses the same dependency providers, so a single `configure()` call covers both routers. The router endpoints are defined as `POST /bug-reports` (submit) and `GET /` + `/reports` + `/{id}` (viewer), so mount `submit_router` at the **parent** prefix (`/api`) — not at `/api/bug-reports`, which would double-segment the URL.

### Pattern 1: admin-only viewer, public submit (most common)

End-users can submit reports anonymously; only admins can read them.

```python
# FastAPI
from fastapi import APIRouter, Depends
from bug_fab.routers import submit as submit_module
from .auth import require_admin

submit_module.configure(storage=storage)

admin = APIRouter(dependencies=[Depends(require_admin)])
admin.include_router(bug_fab.viewer_router, prefix="/bug-reports")

app.include_router(admin, prefix="/admin")
app.include_router(bug_fab.submit_router, prefix="/api")
```

### Pattern 2: auth required everywhere

Internal tools, dashboards behind SSO, anything where the page itself
is gated.

```python
# FastAPI
from fastapi import APIRouter, Depends
from bug_fab.routers import submit as submit_module
from .auth import require_login

submit_module.configure(storage=storage)

protected = APIRouter(dependencies=[Depends(require_login)])
protected.include_router(bug_fab.submit_router, prefix="/api")
protected.include_router(bug_fab.viewer_router, prefix="/admin/bug-reports")
app.include_router(protected)
```

### Pattern 3: no auth (hobby projects, POCs)

```python
# FastAPI
from bug_fab.routers import submit as submit_module

submit_module.configure(storage=storage)

app.include_router(bug_fab.submit_router, prefix="/api")
app.include_router(bug_fab.viewer_router, prefix="/admin/bug-reports")
```

### Other frameworks (sketches)

**Flask** — wrap your blueprints in your existing `@login_required`
decorator and register them under different prefixes:

```python
admin_bp = Blueprint("bug-fab-admin", __name__, url_prefix="/admin/bug-reports")
admin_bp.before_request(require_admin)
register_bug_fab_viewer_routes(admin_bp, storage)
app.register_blueprint(admin_bp)
```

**Express** — mount your existing auth middleware on the viewer prefix:

```js
app.use("/admin/bug-reports", requireAdmin, bugFabViewer(storage));
app.use("/api/bug-reports", bugFabSubmit(storage));
```

**Razor Pages** — gate the viewer area in `Program.cs` with the
existing `[Authorize(Roles="Admin")]` attribute on the page model;
leave the submit endpoint open or behind a lighter policy.

The trade-off across all three patterns: **Bug-Fab cannot ask "who is
logged in"** in v0.1. The viewer therefore cannot display submitter
identity unless your submit handler enriches the metadata payload
with the logged-in user's name/email before forwarding to the storage
backend. That gap closes when `AuthAdapter` lands in v0.2.

## Viewer permissions config

Mount-point auth gates **whether the viewer is reachable**. The
`viewer_permissions` config gates **what destructive actions are
exposed** once a viewer is reachable. Useful when you want a manager
role to see reports but not delete them.

```python
from bug_fab.routers import submit as submit_module

storage = bug_fab.FileStorage(storage_dir="./bug_reports")
settings = bug_fab.Settings.from_env(
    viewer_permissions={
        "can_edit_status": False,  # hide inline status editor
        "can_delete": False,       # hide delete buttons + reject DELETE
        "can_bulk": False,         # hide bulk-close-fixed / bulk-archive
    },
)
submit_module.configure(storage=storage, settings=settings)

app.include_router(bug_fab.viewer_router, prefix="/admin/bug-reports")
```

Defaults are **all `true`** when the viewer is enabled. Set any subset
to `false` to lock down a read-only mount. To turn off the viewer
entirely (intake-only deployment), set `viewer_enabled=false`:

```python
settings = bug_fab.Settings.from_env(viewer_enabled=False)
```

## Rate limiting

Bug-Fab ships a per-IP rate limiter on the intake endpoint, **off by
default**. v0.1 has no `AuthAdapter` so per-user rate limiting is not
yet possible — per-IP is the closest available proxy. It's bypassable
via NAT/VPN/shared offices, so treat it as accidental-flood protection
rather than user accountability.

```bash
BUG_FAB_RATE_LIMIT_ENABLED=true
BUG_FAB_RATE_LIMIT_MAX=50              # requests per window per IP
BUG_FAB_RATE_LIMIT_WINDOW_SECONDS=3600 # 1 hour
```

When exceeded, the intake endpoint returns `429 Too Many Requests`.

**Per-user rate limiting** lands in v0.2 alongside `AuthAdapter`. At
that point the default flips from off-by-default to per-user with a
sane cap; per-IP becomes a fallback for unauthenticated submit
endpoints.

When to enable today:

- **Public-facing POC or demo** — yes, always (the public Bug-Fab POC
  on Fly.io runs with `BUG_FAB_RATE_LIMIT_ENABLED=true`).
- **Internal tool behind SSO** — usually no (your auth middleware
  already filters bots and abusers).
- **Mixed (logged-in users on a public app)** — yes, with a generous
  cap (~200/hr) so legitimate burst-reporters aren't blocked.

## GitHub Issues sync

Opt-in. When enabled, every submitted bug report becomes a new GitHub
Issue in the configured repo. Status changes propagate: a report moved
to `fixed` or `closed` closes the corresponding issue; moving back to
`open` or `investigating` reopens it.

### Enable

```bash
BUG_FAB_GITHUB_ENABLED=true
BUG_FAB_GITHUB_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BUG_FAB_GITHUB_REPO=your-org/your-repo
BUG_FAB_GITHUB_API_BASE=https://api.github.com   # override for GHE
```

The PAT needs `repo` scope (or fine-grained equivalent: read/write
on Issues for the target repo).

### Failure-doesn't-block guarantee

If the GitHub API call fails — rate limit, network blip, expired
PAT — the submission **still succeeds**. The response shape returns
`github_issue_url: null` and the failure is logged server-side. This
is a hard guarantee in the protocol so consumers never lose a report
because GitHub had a bad five seconds.

### Label semantics

The integration does not auto-create labels in v0.1. If you want
severity-tagged issues, pre-create labels in your repo (`severity:low`,
`severity:medium`, etc.) — Bug-Fab will apply them when present. A
configurable label-mapping lands in v0.2.

## html2canvas vendoring

Bug-Fab ships `html2canvas.min.js` **vendored** inside the static
bundle (pinned version, MIT license notice preserved). There is no CDN
dependency and no consumer-supplied path option in v0.1.

Why this matters:

- **Air-gapped consumers** (factory deployments, embedded devices,
  isolated networks) work out of the box. No "punch a CDN hole in the
  firewall" step.
- **Privacy-sensitive consumers** can audit the entire frontend
  surface — there are zero third-party network calls when the FAB is
  used.
- **Deterministic rendering** — pinning the version means a screenshot
  of the same page on the same browser produces the same PNG across
  consumers. Useful for visual regression testing.

The bundle cost is ~150 KB minified, paid once on first page load (or
not at all if you set `defer` and the user never opens the FAB on a
given session). An opt-out build for SPA consumers who supply their
own html2canvas is a v0.2 candidate; today, you get the vendored
version.

## Remote-collector pattern

For consumers that **cannot host their own backend** — embedded
devices, browser extensions, IoT firmware, or anything where adding a
Python process isn't on the table — the wire protocol already supports
remote collection. The frontend posts to a centralized Bug-Fab
collector running somewhere reachable.

Topology:

```
[ Embedded device / browser extension / IoT firmware ]
          |
          | POST /api/bug-reports  (multipart, per PROTOCOL.md)
          v
[ Centralized Bug-Fab collector — a regular FastAPI app ]
          |
          v
[ Storage backend of choice + optional GitHub Issues sync ]
```

What you need:

1. A Bug-Fab instance running somewhere your devices can reach. The
   standard FastAPI integration from [INSTALLATION.md](INSTALLATION.md)
   works as-is.
2. The frontend bundle, served from the device or extension itself,
   pointing at the collector's URL:
   ```html
   <script src="/local/bug-fab.js" defer></script>
   <script>
     window.BugFabAutoInit = false;
     window.addEventListener("DOMContentLoaded", () => {
       window.BugFab.init({
         submitUrl: "https://bug-fab.example.com/api/bug-reports",
       });
     });
   </script>
   ```
3. CORS configured on the collector to accept the device origin
   (browser extensions need the extension origin allowlisted).
4. Rate limiting **enabled** on the collector — devices on shared
   networks can flood quickly otherwise.

The wire protocol carries the `app` and `environment` fields
specifically so a single collector can serve many devices and keep
their reports cleanly separated. A single Bug-Fab instance can be the
collector for any number of clients.

## Cross-origin intake (CORS)

Bug-Fab ships **no CORS middleware by default** and assumes the common
case: the bug reporter loads from the same origin as the app it
reports against. This is what most consumers want — same-origin avoids
CORS entirely and the browser sends cookies / auth headers without
extra plumbing.

Reach for cross-origin only when:

- You're running the [remote-collector pattern](#remote-collector-pattern)
  (collector on a different host than the reporting clients).
- The frontend bundle is served from a CDN with a different origin
  than the intake endpoint.
- Browser extensions, embedded devices, or third-party widgets are
  posting to a shared collector.

When you need CORS, add it at the framework layer using whatever your
app already uses. **Allowlist the specific origins that should submit;
do not use a wildcard in production.** A wildcard `*` allows any
website to fire reports at your collector, which both pollutes
storage and burns rate-limit budget.

### FastAPI

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from bug_fab import submit_router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.example.com",
        "chrome-extension://YOUR_EXTENSION_ID",
    ],
    allow_methods=["POST"],          # intake only needs POST
    allow_headers=["Content-Type"],  # multipart needs Content-Type
    allow_credentials=False,         # set True only if you need cookies
    max_age=600,
)
app.include_router(submit_router, prefix="/api")
```

Mount CORS **before** including the Bug-Fab routers so the middleware
wraps them. `allow_credentials=True` requires non-wildcard
`allow_origins` per the CORS spec — Bug-Fab's intake doesn't need
credentials unless your auth middleware does.

### Flask

```python
from flask import Flask
from flask_cors import CORS

app = Flask(__name__)
CORS(
    app,
    resources={
        r"/api/bug-reports": {
            "origins": [
                "https://app.example.com",
                "chrome-extension://YOUR_EXTENSION_ID",
            ],
            "methods": ["POST"],
            "allow_headers": ["Content-Type"],
        }
    },
)
```

Scope the resource pattern to the intake path so the rest of the app
is unaffected. `flask-cors` is an optional dependency you install
yourself; Bug-Fab does not pull it in.

### Other stacks

The same allowlist principle applies. Adapter authors writing in
ASP.NET Core, Express, SvelteKit, etc. should use their framework's
standard CORS middleware and follow the same rules: explicit origins,
restrict methods to what the intake needs, scope to the intake path.

### Verifying

After configuration, confirm with a quick `curl` from a non-allowlisted
origin:

```bash
curl -i -X OPTIONS https://your-collector.example.com/api/bug-reports \
  -H "Origin: https://evil.example.com" \
  -H "Access-Control-Request-Method: POST"
```

You should see no `Access-Control-Allow-Origin` header in the response
(or an explicit denial). A response that echoes `evil.example.com`
means your allowlist is too loose.

## Content Security Policy (CSP)

If your app sends a `Content-Security-Policy` header — and you should,
if you're serving anything to the public internet — Bug-Fab needs a
small set of allowances to work. This section lists exactly what each
directive needs and gives you a copy-paste-ready baseline.

### What Bug-Fab does, mapped to CSP directives

The frontend bundle does four things that intersect with CSP:

1. Loads `bug-fab.js` and (lazily, on first FAB click) the vendored
   `html2canvas.min.js`. → `script-src`
2. Injects a single `<style id="bug-fab-styles">` tag into `<head>`
   containing the overlay CSS. → `style-src`
3. Renders the captured screenshot inside the annotation canvas via
   a `data:` image URL produced by the canvas's `toDataURL()`. →
   `img-src`
4. POSTs the multipart bug report to the configured `submitUrl`. →
   `connect-src`

The bundle does **not** use `eval`, `Function()`, inline event
handlers, inline `<script>` tags, or any other construct that would
require `'unsafe-eval'` or `'unsafe-inline'` for scripts.

### Required directives

| Directive | What it needs | Why |
|---|---|---|
| `script-src` | The Bug-Fab bundle origin (`'self'` if served same-origin) and the html2canvas origin (same origin as the bundle by default). | Loads `bug-fab.js` and the vendored `html2canvas.min.js`. No `'unsafe-eval'` needed — html2canvas is plain JS. |
| `style-src` | `'self'` plus `'unsafe-inline'` (or a CSP nonce — see below). | The bundle injects one `<style>` tag at runtime for scoped CSS. Without `'unsafe-inline'` the overlay renders unstyled. |
| `img-src` | `'self'` plus `data:`. | The annotation canvas re-loads the screenshot from a `toDataURL("image/png")` blob, which is a `data:` URI. |
| `connect-src` | The origin of the configured `submitUrl`. | The submit POST hits this URL; CSP blocks it if not allowed. |

### Optional / hardening directives

| Directive | Recommended value | Why |
|---|---|---|
| `frame-ancestors` | `'none'` (or your own origin) | If you've mounted the viewer, prevents other sites from iframing it. The viewer is not designed to be embedded. |
| `default-src` | `'self'` | Sensible fallback for anything not explicitly listed above. |
| `object-src` | `'none'` | Bug-Fab does not use `<object>` / `<embed>`. |

### Copy-paste baseline

Same-origin Bug-Fab (the most common case — bundle, intake, and
viewer all under one host):

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  connect-src 'self';
  object-src 'none';
  frame-ancestors 'none'
```

Cross-origin Bug-Fab (remote-collector pattern — frontend on
`app.example.com`, collector on `bug-fab.example.com`):

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  connect-src 'self' https://bug-fab.example.com;
  object-src 'none';
  frame-ancestors 'none'
```

### About the `'unsafe-inline'` for `style-src`

`'unsafe-inline'` for styles is a real tradeoff. It widens your CSP
surface to allow inline `<style>` tags and inline `style=""`
attributes from anywhere in the document, not only from Bug-Fab.

There are two paths to drop it:

1. **Pre-load the empty placeholder stylesheet** at
   `static/bug-fab.css` and override `STYLES` inside the bundle to an
   empty string in your build. The bundle stops injecting a `<style>`
   tag and your existing `style-src 'self'` covers the
   externally-loaded CSS file. This is supported today but requires a
   small build step on your side, since the v0.1 bundle ships with
   `STYLES` non-empty by default.
2. **Wait for CSP-nonce support.** A first-class nonce escape hatch
   is a v0.2 candidate — the bundle would accept a nonce passed via
   config (or read from a meta tag) and stamp it on the injected
   `<style>` tag. That removes the `'unsafe-inline'` requirement
   without any build-time changes. If this matters to your
   deployment, add a +1 (or open) the corresponding GitHub issue so
   it gets prioritized.

For most deployments — internal tools, hobby projects, the public
POC — the `'unsafe-inline'` cost is acceptable for v0.1.

### Verifying your CSP

After deploying a CSP header, exercise the FAB end to end:

1. Click the FAB. The overlay should appear with the page screenshot
   visible inside the annotation canvas. **If the overlay renders
   unstyled**, your `style-src` is too tight (probably missing
   `'unsafe-inline'`). **If the screenshot is a broken image**, your
   `img-src` is too tight (probably missing `data:`).
2. Submit a report. The browser DevTools Network tab should show a
   `POST` to `submitUrl` returning `201`. **If the request never
   leaves the browser**, your `connect-src` is too tight (the
   collector origin is not allowed).
3. Watch the browser console for `Refused to load the script`,
   `Refused to apply inline style`, or `Refused to connect to`
   messages. CSP violations are loud — every refusal logs a clear
   error pointing at the directive that blocked it.

A `Content-Security-Policy-Report-Only` header is a useful first
step: ship it, exercise the FAB, watch the console for refusals,
then promote to enforcing once it's quiet.

## Recommended baselines

| Deployment shape | Storage | Mount-point auth | Rate limit | GitHub sync |
|---|---|---|---|---|
| Public POC / demo | `FileStorage` | None | **Enabled** | Off |
| Internal SaaS, single tenant | `SQLiteStorage` | Viewer behind admin role | Off | Optional |
| Internal SaaS, multi-tenant | `PostgresStorage` | Viewer behind admin role | Off | On |
| Embedded / IoT collector | `FileStorage` or SQLite | Viewer behind admin role | **Enabled** | Optional |
| Open-source project, public reporter | `SQLiteStorage` | Viewer behind admin role | **Enabled** | **On** |
