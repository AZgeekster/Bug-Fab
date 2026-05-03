# Deployment Options

How to configure Bug-Fab for a real deployment. The defaults work for
most consumers; this doc covers the knobs you'll want to think about
when standing up a production-grade instance.

Topics:

- [Storage backend choice](#storage-backend-choice)
- [Router mount-point auth pattern](#router-mount-point-auth-pattern)
- [Auth recipes](#auth-recipes)
- [Viewer permissions config](#viewer-permissions-config)
- [Rate limiting](#rate-limiting)
- [GitHub Issues sync](#github-issues-sync)
- [Webhook delivery](#webhook-delivery)
- [html2canvas vendoring](#html2canvas-vendoring)
- [Remote-collector pattern](#remote-collector-pattern)
- [Cross-origin intake (CORS)](#cross-origin-intake-cors)
- [Content Security Policy (CSP)](#content-security-policy-csp)
- [Upgrading between Bug-Fab versions](#upgrading-between-bug-fab-versions)

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
#     db_path="./bug-fab.db",                       # filesystem path, NOT a SQLAlchemy URL
#     screenshot_dir="/var/bug-fab/screenshots",
# )

# from bug_fab.storage import PostgresStorage
# storage = PostgresStorage(
#     dsn="postgresql+psycopg://user:pw@host/dbname",  # SQLAlchemy DSN
#     screenshot_dir="/var/bug-fab/screenshots",
# )
```

Or set `BUG_FAB_STORAGE_DIR` in the environment and use
`Settings.from_env()`. See [INSTALLATION.md](INSTALLATION.md) for the
env-var list.

### Docker — point storage at a mounted volume

When the host app runs in Docker, both the SQLite database file (or the
`FileStorage` directory) and the screenshot directory MUST live on a
mounted volume — otherwise reports disappear on every redeploy.

A canonical Compose snippet for an app that mounts its data on `./data`:

```yaml
services:
  app:
    build: .
    volumes:
      - ./data:/app/data
    environment:
      BUG_FAB_DATA_DIR: /app/data
      # Or, if you instantiate storage explicitly:
      # BUG_FAB_DB_PATH: /app/data/bug-fab.db
      # BUG_FAB_SCREENSHOT_DIR: /app/data/bug_fab_screenshots
```

In `main.py`:

```python
import os
from bug_fab.storage import SQLiteStorage

DATA = os.environ.get("BUG_FAB_DATA_DIR", "./data")
storage = SQLiteStorage(
    db_path=f"{DATA}/bug-fab.db",
    screenshot_dir=f"{DATA}/bug_fab_screenshots",
)
```

The `examples/fastapi-jinja-docker/` reference consumer ships exactly
this pattern.

**Windows note:** Docker Desktop on Windows handles bind-mounts via the
WSL2 backend, so `./data` is treated as a Linux path inside the
container regardless of the host's drive letter. If you're scripting
the deploy from Git Bash and hit `MSYS_NO_PATHCONV` issues with
`docker run -v` paths, prefix the command (or just use Compose, which
handles this transparently).

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

## Auth recipes

The mount-point auth pattern above tells you *where* to attach auth.
This section gives you copy-paste-able snippets for the three most
common auth shapes. Bug-Fab does not ship these helpers — they are
ten-line stock-framework patterns that wire into the existing
`Depends(...)` slot in Pattern 1 / 2 above.

### FastAPI: HTTP Basic (single admin password)

The smallest viable gate for an internal tool. One env var, one
hardcoded user, no database. Good for a hobby app or a private POC;
not appropriate for anything multi-tenant.

```python
import os, secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()
ADMIN_USER = os.environ.get("BUGS_ADMIN_USER", "admin")
ADMIN_PASS = os.environ["BUGS_ADMIN_PASS"]   # crash early if unset

def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> str:
    user_ok = secrets.compare_digest(creds.username, ADMIN_USER)
    pass_ok = secrets.compare_digest(creds.password, ADMIN_PASS)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username
```

Wire it into Pattern 1 above by passing `require_admin` to the viewer
APIRouter's `dependencies=[Depends(...)]`. `secrets.compare_digest`
avoids a timing-leak that lets attackers guess the password one
character at a time.

### FastAPI: cookie session (reuse host app's login)

When the host FastAPI app already has a login form that sets a
session cookie, lift the same lookup into a `Depends(...)` so the
viewer reuses the existing identity.

```python
from fastapi import Cookie, Depends, HTTPException, status
from .auth import lookup_session   # your existing session store

async def require_admin(session_id: str | None = Cookie(default=None)) -> str:
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = await lookup_session(session_id)
    if user is None or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user.username
```

The cookie name (`session_id` here) must match whatever your login
form sets. Pair this with `Pattern 1` from the mount-point section so
end-users can submit without logging in but only admins can view.

### FastAPI: OAuth2 / JWT bearer

For consumers already running a token-based login flow (FastAPI's
own `/token` endpoint, an external IdP, or a third-party SSO),
FastAPI's standard `OAuth2PasswordBearer` recipe drops in unchanged.
The full recipe is documented at
<https://fastapi.tiangolo.com/tutorial/security/> — once you have
`get_current_user`, gate the viewer with:

```python
from fastapi import APIRouter, Depends
from .auth import get_current_user, require_admin_role

admin = APIRouter(dependencies=[
    Depends(get_current_user),
    Depends(require_admin_role),
])
admin.include_router(viewer_router, prefix="/bug-reports")
app.include_router(admin, prefix="/admin")
```

The two-dependency pattern keeps the "is logged in" check independent
of the "is an admin" check — useful when the same JWT scope set
covers multiple roles.

### Flask: `flask-login`

The same delegation pattern works under Flask. Wrap the blueprint
with the standard `@login_required` decorator from
[flask-login](https://flask-login.readthedocs.io/), then add a
role check inside `before_request`:

```python
from flask_login import login_required, current_user
from flask import abort

@admin_bp.before_request
@login_required
def require_admin():
    if not current_user.is_admin:
        abort(403)

register_bug_fab_viewer_routes(admin_bp, storage)
app.register_blueprint(admin_bp, url_prefix="/admin/bug-reports")
```

### Django: `LoginRequiredMixin` + role check

For the Django reusable app (`bug-fab[django]`), gate the viewer
URLconf in your project's `urls.py` and apply the role check via the
project's existing `UserPassesTestMixin`:

```python
# project/urls.py
from django.contrib.auth.decorators import user_passes_test
from django.urls import include, path

def admin_required(user):
    return user.is_authenticated and user.is_staff

urlpatterns = [
    path(
        "admin/bug-reports/",
        user_passes_test(admin_required, login_url="/login/")(
            include("bug_fab.adapters.django.urls")
        ),
    ),
]
```

This delegates to Django's existing auth machinery — no Bug-Fab-specific
identity model. The `is_staff` check is appropriate for self-hosted
single-tenant deployments; multi-tenant consumers should swap in a
group or permission check that matches their model.

### Across all recipes

These are patterns, not Bug-Fab features. Two rules apply:

- **Bug-Fab v0.1 cannot ask "who is logged in"** — the recipes above
  protect *which* requests reach the routers. They do not feed the
  identity back to Bug-Fab. Submitter attribution remains the
  consumer's responsibility until `AuthAdapter` lands in v0.2.
- **Test the gate before you trust it.** A missing `Depends(...)`
  silently opens the viewer to the public internet. After wiring,
  hit the viewer URL from an incognito window with no cookies — you
  should get a 401 / 403, not the report list.

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

## Webhook delivery

Opt-in generic webhook. When enabled, every successfully persisted
bug report is best-effort `POST`ed as JSON to the URL you configure.
The body is the same `BugReportDetail` shape `GET /reports/{id}`
returns — id, title, severity, status, full context, lifecycle, and
the `github_issue_url` when GitHub sync is also enabled.

This is the universal escape hatch: pipe reports into Slack, Linear,
Pushover, n8n, Zapier, an internal collector, a Kafka producer
sidecar — anything that accepts a JSON POST. No code change to
Bug-Fab; you just point it at a URL.

### Enable

```bash
BUG_FAB_WEBHOOK_ENABLED=true
BUG_FAB_WEBHOOK_URL=https://hooks.example.com/services/T0/B0/abcdef
BUG_FAB_WEBHOOK_HEADERS='{"Authorization": "Bearer xyz", "X-Source": "bug-fab"}'
BUG_FAB_WEBHOOK_TIMEOUT_SECONDS=5.0
```

Or in code:

```python
from bug_fab.config import Settings
settings = Settings(
    webhook_enabled=True,
    webhook_url="https://hooks.example.com/services/T0/B0/abcdef",
    webhook_headers={"Authorization": "Bearer xyz"},
    webhook_timeout_seconds=5.0,
)
```

`BUG_FAB_WEBHOOK_HEADERS` accepts two formats so it survives any
shell / .env file's quoting quirks:

- **JSON object** (recommended): `'{"Authorization": "Bearer xyz", "X-Foo": "bar"}'`
- **Semicolon-separated pairs**: `Authorization=Bearer xyz;X-Foo=bar`

A malformed headers value falls back to `{}` rather than crashing
the process at startup.

### Order of operations on intake

For each successful submission, Bug-Fab runs:

1. `storage.save_report(...)` — local persistence (always succeeds
   or returns 500 to the client).
2. GitHub Issues sync (when configured) — best-effort, populates
   `github_issue_url` on the stored report.
3. Webhook delivery (when configured) — best-effort, fires last so
   the outbound payload includes `github_issue_url` when both
   integrations are enabled.

This ordering means a Slack notification or a Linear ticket can link
directly to the GitHub issue without requiring a follow-up update.

### Failure-doesn't-block guarantee

The webhook follows the same hard contract the GitHub sync does:
**a failed `POST` MUST NOT cause the intake response to be non-2xx.**
Specifically, all of these soft-fail to a structured `WARNING` log
line and leave the 201 response intact:

- 4xx / 5xx response from the webhook receiver
- TCP connect refused, DNS NXDOMAIN
- Request timeout (default 5 seconds, configurable)
- Any other `httpx.HTTPError`

The submission is already persisted by the time the webhook fires;
losing the notification is annoying, losing the report would be a
bug. Bug-Fab is biased toward never losing the report.

### Slack incoming webhook recipe

Slack's incoming webhooks accept a JSON object — they don't require
any of the rich `blocks` / `attachments` shaping. Bug-Fab posts the
full `BugReportDetail` and Slack renders the `text`-equivalent fields
verbatim. For a prettier card, run the JSON through n8n / Zapier /
your own tiny relay and shape it into Slack's preferred
[Block Kit format](https://api.slack.com/block-kit).

```bash
# 1. Create a Slack incoming webhook in your workspace's app settings.
# 2. Copy the URL Slack gives you.
# 3. Set Bug-Fab's webhook config:
BUG_FAB_WEBHOOK_ENABLED=true
BUG_FAB_WEBHOOK_URL=https://hooks.slack.com/services/T0/B0/abcdef
```

### When to skip the webhook and use the GitHub sync instead

If your team already lives in GitHub Issues, the [GitHub Issues
sync](#github-issues-sync) is the more polished path — it auto-
formats the issue body, applies severity labels, and round-trips
status changes back to the upstream issue. The webhook is the
right tool when:

- Your destination isn't GitHub (Linear, Jira, Slack, custom).
- You want the raw payload to feed into a transformation /
  notification pipeline you already run.
- You need to fan out one report to many destinations — chain a
  workflow tool (n8n, Zapier) behind the single webhook URL.

Both can run side-by-side; turning one on doesn't disable the other.

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

## Upgrading between Bug-Fab versions

Most version bumps are no-op for consumers — `pip install -U` and
restart. This section covers the cases that aren't.

### What auto-init covers (and what it doesn't)

`SqlStorageBase.__init__` calls `Base.metadata.create_all(self.engine)`
on every process start. This is **idempotent** — every CREATE TABLE
is wrapped in `IF NOT EXISTS` semantics — so it's safe across
restarts. It handles two cases automatically:

- **Fresh install.** A consumer with no existing Bug-Fab database
  gets the full v0.1 schema on first `submit.configure(storage=...)`
  call. No `alembic upgrade head` step required.
- **Identical schema.** A consumer who restarts after a Bug-Fab patch
  release with no schema changes sees zero database churn.

What auto-init does **not** do: add columns to existing tables, drop
columns, change column types, or rename anything. `create_all` skips
tables that already exist — it never alters them. When a Bug-Fab
release adds a column to `bug_reports` (or any other tracked table),
auto-init alone leaves your database one schema version behind. You
need Alembic for that.

### SQLite / Postgres: `alembic upgrade head`

The package ships a complete Alembic environment at
`bug_fab/storage/_alembic/`. From any consumer project, point the
Alembic CLI at the bundled `alembic.ini`:

```bash
# Resolve the bundled alembic.ini path once
ALEMBIC_INI=$(python -c "import bug_fab.storage._alembic, os; \
  print(os.path.join(os.path.dirname(bug_fab.storage._alembic.__file__), 'alembic.ini'))")

# SQLite
alembic -c "$ALEMBIC_INI" -x url=sqlite:///./bug-fab.db upgrade head

# Postgres
alembic -c "$ALEMBIC_INI" -x url=postgresql+psycopg://user:pw@host/db upgrade head
```

The `-x url=...` argument overrides the default URL inside the
`alembic.ini` and matches the same DSN you pass to
`SQLiteStorage(db_path=...)` / `PostgresStorage(dsn=...)` at runtime.
Alembic records its current revision in the `alembic_version` table —
re-running `upgrade head` after a no-op release is a no-op.

A more ergonomic CLI shipping as `python -m bug_fab.storage.migrate`
is on the v0.2 roadmap. Today, the one-liner above is the supported
path.

### FileStorage: per-version `_migrate.py` script

`FileStorage` writes one directory per report containing
`metadata.json` + `screenshot.png`. There is no schema in the
RDBMS sense — but there *is* a metadata-shape contract, and breaking
changes to that shape need a migration.

The pattern Bug-Fab will commit to from v0.2 onward: every release
that mutates `metadata.json` ships a `_migrate_<from>_to_<to>.py`
script under `bug_fab/storage/_file_migrations/`. Each script:

1. Walks every `<storage_dir>/<report_id>/metadata.json`.
2. Loads, transforms in-memory, writes atomically (temp-file +
   rename) so an interrupted migration leaves the report
   readable in either the old or new shape.
3. Skips files already at the target version (idempotent).

Run from the consumer project:

```bash
python -m bug_fab.storage.file_migrate \
    --storage-dir ./bug_reports \
    --target 0.2.0
```

v0.1 ships with no `_migrate.py` scripts because the metadata shape
is what we're committing to. The first migration will land alongside
whichever v0.2 change first mutates `metadata.json`.

### Test a migration locally before deploying

Whichever backend you use, the same dry-run pattern catches problems:

1. **Snapshot the storage dir.** `cp -r ./bug-reports ./bug-reports.bak`
   (and a SQL `pg_dump` / SQLite file copy for the metadata DB).
2. **Run the migration against the snapshot.** Point the new
   Bug-Fab version at the snapshot directory + restored DB on a
   non-production host. `alembic upgrade head` runs in seconds; a
   FileStorage `_migrate.py` walks every report once.
3. **Smoke-test the viewer.** Hit `/admin/bug-reports`, open three
   reports, exercise status changes. Anything that 500s is a
   migration bug — file an issue with the migration script's stderr
   and the affected report's metadata before applying to production.
4. **Production cutover.** Stop the app, run the migration against
   the live storage, restart. With idempotent migrations a brief
   outage is the only cost; rollback is "restore the snapshot."

A single Bug-Fab consumer can mix `FileStorage` for hobby
deployments and `SQLiteStorage` for production — the dry-run pattern
applies to both.

## Recommended baselines

| Deployment shape | Storage | Mount-point auth | Rate limit | GitHub sync |
|---|---|---|---|---|
| Public POC / demo | `FileStorage` | None | **Enabled** | Off |
| Internal SaaS, single tenant | `SQLiteStorage` | Viewer behind admin role | Off | Optional |
| Internal SaaS, multi-tenant | `PostgresStorage` | Viewer behind admin role | Off | On |
| Embedded / IoT collector | `FileStorage` or SQLite | Viewer behind admin role | **Enabled** | Optional |
| Open-source project, public reporter | `SQLiteStorage` | Viewer behind admin role | **Enabled** | **On** |
