# AGENTS.md — Bug-Fab for AI Assistants

## What this file is

This file is for **AI coding assistants** (Claude Code, Cursor, Gemini Code
Assist, ChatGPT, Aider, etc.) and the humans driving them. It covers the
three things you are most likely to be asked to do with Bug-Fab: integrate
it into a consumer app, write a non-Python adapter, or contribute to
Bug-Fab itself.

> **If you only read one Bug-Fab doc, read this.**
> **If you have time for two, read this and [`docs/PROTOCOL.md`](docs/PROTOCOL.md).**

The protocol spec is the binding contract; this file is the curated entry
point that points at the right doc for whatever you are about to do.

---

## What Bug-Fab is in five bullets

- A **bug-reporting tool** that drops into any web app via a single
  `<script>` tag. End-user clicks a floating action button, the page
  snapshots itself, the user annotates and types, the bundle POSTs.
- The wire is the contract: `POST /bug-reports` with `multipart/form-data`
  carrying JSON `metadata` plus a PNG `screenshot`. Console errors and
  recent network calls ride along automatically.
- **Framework-agnostic.** The vanilla-JS frontend works in HTMX, Razor,
  Express, Flask, FastAPI, SvelteKit, etc. The protocol is the spec;
  FastAPI is the reference adapter; other adapters live as documentation
  sketches in v0.1 and as first-class packages from v0.2 onward.
- v0.1 ships: a Python package (`pip install --pre bug-fab`), three
  storage backends (file / SQLite / Postgres), submit + viewer routers,
  status workflow, optional GitHub Issues sync, vendored `html2canvas`,
  and a pytest plugin for adapter conformance.
- **Out of scope forever:** telemetry, automatic error monitoring,
  full issue-tracking workflow, support chat, A/B testing, hosted SaaS.
  Bug-Fab integrates with those tools; it does not replace them. See
  [`docs/ROADMAP.md`](docs/ROADMAP.md) § "Out of scope — forever."

---

## Common AI tasks

### Task A — Add Bug-Fab to a FastAPI consumer

The reference path. About ten lines of code. The router pattern uses a
module-level `configure(storage=...)` call, not a factory function:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import bug_fab
from bug_fab.routers import submit, viewer

app = FastAPI()
storage = bug_fab.FileStorage(storage_dir="./bug_reports")

# Wire the router with a storage backend. The viewer reads its
# storage / settings / github-sync from the same module-level
# handles, so this single call wires both routers.
submit.configure(storage=storage)

# The router owns the "/bug-reports" suffix; mount under /api to get
# the canonical /api/bug-reports endpoint.
app.include_router(submit.submit_router, prefix="/api")
app.include_router(viewer.viewer_router, prefix="/admin/bug-reports")

# Serve the frontend bundle (FAB + overlay + vendored html2canvas).
app.mount("/bug-fab/static", StaticFiles(packages=["bug_fab"]),
          name="bug-fab-static")
```

Then add the bundle to your base template:

```html
<script src="/bug-fab/static/bug-fab.js" defer></script>
<script>
  window.addEventListener("DOMContentLoaded", () => {
    window.BugFab.init({ submitUrl: "/api/bug-reports" });
  });
</script>
```

**Common pitfalls AIs hit:**
- Forgetting `submit.configure(storage=...)` before `include_router`.
  The endpoints will 500 because the storage handle is `None`.
- Mounting the submit router under `/api/bug-reports` (the router
  already owns the `/bug-reports` suffix — that gets you a double-prefix).
- Calling async storage methods (`await storage.save_report(...)`) from
  sync code without wrapping in `asyncio.run`. The routers handle this
  themselves; only matters if you reach into storage directly.
- Setting `window.BugFabConfig = {...}` instead of calling
  `window.BugFab.init({...})`. There is no `BugFabConfig` global — the
  bundle takes config through `init()`.

**Where to look next:** [`docs/INSTALLATION.md`](docs/INSTALLATION.md),
[`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md),
[`examples/fastapi-minimal/main.py`](examples/fastapi-minimal/main.py).

---

### Task B — Add Bug-Fab to a Flask consumer

There is **no Flask Blueprint shim in v0.1.** A first-party Flask
adapter is on the v0.2 roadmap. Today, Flask consumers implement the
protocol directly using `bug_fab.FileStorage` plus the Pydantic schemas
(`BugReportCreate`):

```python
from flask import Flask, request, jsonify, send_from_directory
from importlib.resources import files
import asyncio, json
from datetime import UTC, datetime
from pydantic import ValidationError
from bug_fab import FileStorage, BugReportCreate

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024
storage = FileStorage(storage_dir="./bug_reports")

@app.post("/api/bug-reports")
def submit():
    metadata_raw = request.form.get("metadata")
    screenshot_file = request.files.get("screenshot")
    if not metadata_raw or screenshot_file is None:
        return jsonify({"error": "validation_error",
                        "detail": "metadata and screenshot required"}), 400
    try:
        payload = BugReportCreate.model_validate(json.loads(metadata_raw))
    except ValidationError as exc:
        return jsonify({"error": "schema_error", "detail": exc.errors()}), 422

    metadata = payload.model_dump(mode="json")
    metadata["server_user_agent"] = request.headers.get("User-Agent", "")
    report_id = asyncio.run(storage.save_report(metadata, screenshot_file.read()))
    return jsonify({
        "id": report_id,
        "received_at": datetime.now(UTC).isoformat(),
        "stored_at": f"file://{storage.storage_dir.as_posix()}/{report_id}/",
        "github_issue_url": None,
    }), 201

@app.get("/bug-fab/static/<path:asset>")
def static_bundle(asset):
    return send_from_directory(str(files("bug_fab").joinpath("static")), asset)
```

This is **the bare minimum.** Strict severity validation, rate limiting,
viewer pages, status workflow, bulk operations, and GitHub sync all need
to be added by hand for full v0.1 conformance — use the FastAPI router
under `bug_fab/routers/` as a reference.

**Common pitfalls AIs hit:**
- Looking for `bug_fab.adapters.flask` Blueprint — it does not exist
  in v0.1.
- Forgetting that `Storage.save_report` is async. Wrap with
  `asyncio.run(...)` inside sync Flask handlers, or run Flask through
  an ASGI shim (`asgiref`, `hypercorn`).
- Coercing invalid `severity` to `"medium"`. Bug-Fab forbids this —
  reject with 422 instead. See Task D for context.

**Where to look next:** [`examples/flask-minimal/main.py`](examples/flask-minimal/main.py),
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

---

### Task C — Add Bug-Fab to a React or SPA consumer

The vanilla-JS bundle is framework-agnostic — drop a `<script>` tag,
call `window.BugFab.init(...)`, done. There is **no `npm install
bug-fab` in v0.1** (an npm publish is on the v0.2 roadmap). Either
proxy `/bug-fab/bug-fab.js` to a Bug-Fab backend, or copy
`static/bug-fab.js` and `static/vendor/html2canvas.min.js` into your
own `public/` directory.

The simplest React integration:

```tsx
import { useEffect } from "react";

export function BugFab() {
  useEffect(() => {
    const script = document.createElement("script");
    script.src = "/bug-fab/bug-fab.js";
    script.defer = true;
    script.onload = () => {
      window.BugFab?.init({
        submitUrl: "/api/bug-reports",
        appVersion: "1.2.3",
        environment: "prod",
      });
    };
    document.head.appendChild(script);
    return () => window.BugFab?.destroy();
  }, []);
  return null;
}
```

A reference React provider with `useBugFab()` hook ships in
[`examples/react-spa/src/BugFabProvider.tsx`](examples/react-spa/src/BugFabProvider.tsx) —
copy it for ergonomic integration with `useEffect`-style React.

**Common pitfalls AIs hit:**
- Trying to `npm install bug-fab` — not on npm in v0.1; planned v0.2.
- React 18 StrictMode double-invokes effects in dev. The bundle's
  `init()` is idempotent (second call is a no-op), but you should still
  guard the `<script>` tag with a module-scope `Promise` to avoid
  double-injection. The reference provider handles this.
- Calling `window.BugFab.init()` before the script loads. Use the
  `script.onload` callback or the reference provider.
- Forgetting `window.BugFab?.destroy()` on unmount — leaves the FAB
  in the DOM and the patched `window.fetch` in place.

**Where to look next:**
[`examples/react-spa/`](examples/react-spa/),
[`static/README.md`](static/README.md).

---

### Task D — Write a new adapter (Razor, Express, SvelteKit, Go, etc.)

The wire protocol is the spec. The Python adapter is one reference
implementation. Any HTTP server in any language can be a Bug-Fab
adapter as long as it honors the protocol.

**Required reading, in order:**

1. [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — the binding spec. Endpoints,
   field names, error codes, enum values.
2. [`docs/ADAPTERS.md`](docs/ADAPTERS.md) — code-level sketches for
   ASP.NET Core / Razor Pages, Express, SvelteKit, and Go. Use these as
   starting points.
3. [`docs/CONFORMANCE.md`](docs/CONFORMANCE.md) — how to run the pytest
   plugin against your adapter to verify protocol compliance.

**Critical rules every adapter MUST honor:**

1. **Reject invalid severity with 422; never coerce.** Severity is
   locked to `low | medium | high | critical`. A real-world .NET
   reference implementation tested against silently coerced unknown
   values to `"medium"` — Bug-Fab v0.1 explicitly forbids this. The
   conformance test `test_intake_invalid_severity` will fail your
   adapter if you do this.
2. **Accept deprecated enum values on read forever.** If a future
   protocol revision retires a status value, your adapter still must be
   able to display reports stored with the old value. Adapters MAY
   reject deprecated values on write; they MUST accept them on read.
3. **Screenshot stays on disk; never blob-in-DB.** Even SQL adapters
   write the PNG to a configured `screenshot_dir` and store the path
   in the row. Keeps row sizes small and screenshots servable as
   static files.
4. **GitHub Issues sync failure must NOT block submission.** If GitHub
   is down, the submission still succeeds with `github_issue_url:
   null` — failures log server-side. Wrap the GitHub call in
   try/catch and never let it propagate.
5. **Capture User-Agent from the request header.** That is the source
   of truth. Preserve any client-supplied value as
   `client_reported_user_agent` separately for diagnostics.

**Common pitfalls AIs hit:**
- Silently coercing invalid severity (most common conformance
  failure).
- Skipping the deprecated-values rule (read paths reject what your
  current write enum does not contain — locks consumers out of
  historical data).
- Letting a database transaction wrap the GitHub PATCH call — GitHub
  slowness will hold your DB locks.
- Defaulting to `camelCase` JSON because that is the stack default.
  The wire protocol is `snake_case` everywhere.

**Where to look next:** [`docs/PROTOCOL.md`](docs/PROTOCOL.md),
[`docs/ADAPTERS.md`](docs/ADAPTERS.md),
[`docs/CONFORMANCE.md`](docs/CONFORMANCE.md).

---

### Task E — Configure storage backends

| Backend | When to pick it | Extra deps |
|---|---|---|
| `FileStorage` | Hobby projects, single-process apps, anywhere you would rather not run a database. Backup = `tar` the directory. | None |
| `SQLiteStorage` | You want list/filter/search performance without running a DB server. Excellent for self-hosted apps with one box. | `pip install --pre "bug-fab[sqlite]"` |
| `PostgresStorage` | You are already running Postgres. Reports become a real table you can join. | `pip install --pre "bug-fab[postgres]"` |

```python
# File backend (default; zero external deps)
from bug_fab import FileStorage
storage = FileStorage(storage_dir="/var/bug-fab/reports")

# SQLite (lazy import — requires bug-fab[sqlite])
from bug_fab.storage import SQLiteStorage
storage = SQLiteStorage(
    db_url="sqlite:///./bug-fab.db",
    screenshot_dir="/var/bug-fab/screenshots",
)

# Postgres (lazy import — requires bug-fab[postgres])
from bug_fab.storage import PostgresStorage
storage = PostgresStorage(
    db_url="postgresql+psycopg://user:pw@host/dbname",
    screenshot_dir="/var/bug-fab/screenshots",
)
```

**Common pitfalls AIs hit:**
- Trying to put screenshots in the DB. Bug-Fab never does this —
  screenshots are always on disk regardless of metadata backend. The
  SQL backends store metadata in the DB plus a `screenshot_path`
  column.
- Importing SQL backends from the top-level `bug_fab` namespace.
  They are lazy-imported via `bug_fab.storage` so that SQLAlchemy
  stays an optional install. Use `from bug_fab.storage import
  SQLiteStorage` (not `from bug_fab import SQLiteStorage`).
- Forgetting to mount `BUG_FAB_STORAGE_DIR` to a persistent volume in
  containerized deployments — screenshots disappear on redeploy
  otherwise.

**Where to look next:**
[`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) § "Storage
backend choice."

---

### Task F — Configure auth (mount-point delegation in v0.1)

Bug-Fab v0.1 ships **no `AuthAdapter`.** Auth is delegated to consumer
middleware via the URL prefix where the routers are mounted. The proper
ABC lands in v0.2.

**Pattern 1: open submit, admin-only viewer (most common).**

```python
from fastapi import APIRouter, Depends
from .auth import require_admin
from bug_fab.routers import submit, viewer

submit.configure(storage=storage)

admin = APIRouter(dependencies=[Depends(require_admin)])
admin.include_router(viewer.viewer_router, prefix="/bug-reports")

app.include_router(admin, prefix="/admin")
app.include_router(submit.submit_router, prefix="/api")
```

**Pattern 2: auth required everywhere.**

```python
protected = APIRouter(dependencies=[Depends(require_login)])
protected.include_router(submit.submit_router, prefix="/api")
protected.include_router(viewer.viewer_router, prefix="/admin/bug-reports")
app.include_router(protected)
```

**Pattern 3: no auth (POCs, hobby projects).**

```python
app.include_router(submit.submit_router, prefix="/api")
app.include_router(viewer.viewer_router, prefix="/admin/bug-reports")
```

**Fine-grained gating WITHIN an authenticated viewer mount** uses
`viewer_permissions`. This is **not a substitute for auth itself** —
it gates which destructive endpoints are exposed once the viewer is
already reachable:

```python
settings = bug_fab.Settings.from_env(viewer_permissions={
    "can_edit_status": False,
    "can_delete": False,
    "can_bulk": False,
})
```

**Common pitfalls AIs hit:**
- Looking for an `AuthAdapter` class — it does not exist in v0.1.
  v0.2 will add `AuthAdapter` once real consumer integrations reveal
  the right shape.
- Treating `viewer_permissions` as a substitute for auth. It only
  gates destructive endpoints within an already-reachable viewer; the
  viewer itself is reachable to anyone the mount-point allows in.
- Wrapping the submit router in heavy auth on a public-facing
  consumer. Most teams want anonymous submission and admin-only
  viewing — that is Pattern 1.

**Where to look next:**
[`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) § "Router
mount-point auth pattern."

---

### Task G — Configure GitHub Issues sync

Opt-in. When enabled, every submitted bug becomes a new GitHub Issue;
status changes propagate (`fixed` / `closed` close the issue;
`open` / `investigating` reopen it).

```bash
BUG_FAB_GITHUB_ENABLED=true
BUG_FAB_GITHUB_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BUG_FAB_GITHUB_REPO=your-org/your-repo
BUG_FAB_GITHUB_API_BASE=https://api.github.com   # override for GHE
```

The PAT needs `repo` scope (or fine-grained equivalent: read/write on
Issues for the target repo).

**Failure-doesn't-block guarantee.** If the GitHub API call fails (rate
limit, network blip, expired PAT), the submission still succeeds. The
response shape returns `github_issue_url: null` and the failure logs
server-side. This is a **hard guarantee in the protocol** so consumers
never lose a report because GitHub had a bad five seconds.

**Common pitfalls AIs hit:**
- Assuming GitHub failures will block submission. They do not — and
  must not, per the protocol. If you need GitHub-required submission,
  add a custom layer above Bug-Fab.
- Auto-creating labels. v0.1 does not — pre-create severity labels
  (`severity:low`, `severity:medium`, etc.) in your repo. Bug-Fab
  applies them when present. A configurable label-mapping lands in
  v0.2.

**Where to look next:**
[`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) § "GitHub
Issues sync."

---

### Task H — Configure rate limiting

Bug-Fab ships a per-IP rate limiter on the intake endpoint, **off by
default.**

```bash
BUG_FAB_RATE_LIMIT_ENABLED=true
BUG_FAB_RATE_LIMIT_MAX=50              # requests per window per IP
BUG_FAB_RATE_LIMIT_WINDOW_SECONDS=3600 # 1 hour
```

When exceeded, intake returns `429 Too Many Requests`.

**Common pitfalls AIs hit:**
- Assuming per-user rate limits. v0.1 is **per-IP only** — Bug-Fab
  has no `AuthAdapter`, so it cannot ask "who is this." Per-user lands
  in v0.2 alongside `AuthAdapter`.
- Treating per-IP as user accountability. It is bypassable via NAT,
  VPN, and shared offices — it is accidental-flood protection only.
- Enabling it on internal SSO-protected tools. Your auth middleware
  already filters bots and abusers; the limiter just creates noise.

**Where to look next:**
[`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) § "Rate
limiting."

---

### Task I — Run conformance tests against an adapter

The conformance plugin ships with the package. It tests over HTTP, so
the adapter under test can be in any language.

```bash
pip install bug-fab    # or "bug-fab[conformance]" for the slim install
pytest --bug-fab-conformance --base-url=https://my-app.example.com/bug-fab
```

Optional flags:

```bash
--auth-header="Bearer eyJ..."   # if your adapter requires auth
--skip-mutating                 # skip POST/PUT/DELETE; read-only smoke test
```

**Common pitfalls AIs hit:**
- Running `pytest` without the `--bug-fab-conformance` flag. The
  plugin skips the conformance suite without it.
- Not seeding the deprecated-values fixture before
  `test_deprecated_values`. The plugin pre-seeds via your adapter's
  fixture-seed hook if you have one; without it, the test skips with a
  documented warning and you should manually verify the rule by
  inserting a deprecated-value record and confirming round-trip.
- Pointing `--base-url` at the route prefix instead of the host root.
  The plugin appends the protocol's documented paths
  (`/bug-reports`, `/reports`, etc.) to whatever base URL you give it.

**Where to look next:**
[`docs/CONFORMANCE.md`](docs/CONFORMANCE.md).

---

### Task J — Contribute to Bug-Fab itself

Read [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md). The short form:

```bash
git clone https://github.com/YOUR-USERNAME/Bug-Fab.git
cd Bug-Fab
python -m venv .venv && source .venv/Scripts/activate
pip install -e ".[dev]"
pre-commit install
ruff check . && ruff format . && pytest
```

Pre-commit runs three hooks: `forbidden-strings` (blocks private
identifiers from leaking into the public repo), `ruff check`, and
`ruff format --check`. CI runs Ruff plus pytest on Python 3.10 / 3.11
/ 3.12 with an 85% coverage gate (100% on the protocol-validation
layer).

**v0.1 scope is frozen.** New features land in v0.2 and beyond. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for what is in flight and what is
explicitly out of scope. For larger changes, please open an issue first
to align on the shape before investing time.

---

## The contract — load-bearing facts an AI must NOT violate

These are the protocol invariants. If your adapter or integration
breaks one of these, it is wrong (not the spec):

1. **Severity enum is locked:** `low | medium | high | critical`.
   Adapters MUST reject invalid values with `422`. Silent coercion
   fails conformance.
2. **Status enum is locked:** `open | investigating | fixed | closed`.
   Same MUST-reject rule on write paths.
3. **Deprecated values stay legal-on-read forever.** Adapters MUST
   accept stored data carrying deprecated enum values; MAY reject them
   on write. This rule keeps long-lived stores readable across
   protocol revisions.
4. **`protocol_version` is required in metadata.** v0.1 protocol is
   the literal string `"0.1"`. Missing or unknown values yield
   `400 unsupported_protocol_version`.
5. **Screenshots are always on disk.** Never blob-in-DB. SQL backends
   store metadata in the DB plus a `screenshot_path` column.
6. **Server captures `User-Agent` from the request header as source of
   truth.** Client-supplied value is preserved as a separate
   `client_reported_user_agent` field; never overwrite the
   server-captured value with the client value.
7. **GitHub Issues sync failure MUST NOT block submission.**
   Best-effort, logged; response returns `github_issue_url: null` on
   failure. Hard protocol guarantee.
8. **Mount-point auth delegation in v0.1.** Bug-Fab ships no
   `AuthAdapter`; consumers protect routes via the URL prefix where
   the routers are mounted. `AuthAdapter` lands in v0.2.
9. **The wire protocol is the contract.** The FastAPI adapter is one
   reference implementation. Adapters in other languages MUST honor
   the protocol; they SHOULD pass `bug-fab-conformance` tests.
10. **Severity and status values are case-sensitive lowercase.** No
    `"Low"`, no `"OPEN"`, no `"In_Progress"`. The conformance suite
    rejects any other casing.

---

## Where to look next — pointer table

| What you need | Where to look |
|---|---|
| Wire protocol spec (the binding contract) | [`docs/PROTOCOL.md`](docs/PROTOCOL.md) |
| Install paths per framework | [`docs/INSTALLATION.md`](docs/INSTALLATION.md) |
| Storage / auth / GitHub / rate-limit config | [`docs/DEPLOYMENT_OPTIONS.md`](docs/DEPLOYMENT_OPTIONS.md) |
| Adapter sketches (Razor, Express, SvelteKit, Go) | [`docs/ADAPTERS.md`](docs/ADAPTERS.md) |
| Conformance plugin guide | [`docs/CONFORMANCE.md`](docs/CONFORMANCE.md) |
| Common adoption questions | [`docs/FAQ.md`](docs/FAQ.md) |
| Contributing guide | [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) |
| Public roadmap | [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Security model + reporting policy | [`SECURITY.md`](SECURITY.md) |
| Migration between versions | [`docs/UPGRADE.md`](docs/UPGRADE.md) |
| Release-by-release change log | [`CHANGELOG.md`](CHANGELOG.md) |
| Public POC self-hosting on Fly.io | [`docs/POC_HOSTING.md`](docs/POC_HOSTING.md) |
| Python public API surface | [`bug_fab/__init__.py`](bug_fab/__init__.py) (read its `__all__`) |
| Frontend bundle public API | [`static/README.md`](static/README.md) (and `window.BugFab` block in `static/bug-fab.js`) |
| FastAPI integration example | [`examples/fastapi-minimal/main.py`](examples/fastapi-minimal/main.py) |
| Flask integration example | [`examples/flask-minimal/main.py`](examples/flask-minimal/main.py) |
| React provider example | [`examples/react-spa/src/BugFabProvider.tsx`](examples/react-spa/src/BugFabProvider.tsx) |
| Live POC to play with | URL pending — see the README badge once Fly.io deploy is live |

---

## Things AIs commonly get wrong (anti-patterns)

A short list of failure modes that have shown up in AI-generated
integrations against Bug-Fab. Avoid these:

- **Trying to `npm install bug-fab`.** Not on npm in v0.1. Use the
  `<script>` tag pattern; npm publish lands in v0.2.
- **Looking for `bug_fab.adapters.flask` Blueprint.** Does not exist
  in v0.1. Flask consumers implement the protocol directly using
  `bug_fab.FileStorage` plus the Pydantic schemas.
- **Looking for an `AuthAdapter` class.** Does not exist in v0.1. Auth
  is delegated to consumer middleware via the URL prefix where the
  routers are mounted. `AuthAdapter` lands in v0.2.
- **Trying to put screenshots in the DB.** Always on disk regardless
  of metadata backend.
- **Silently coercing invalid `severity` to `"medium"`.** Reject with
  `422`. The conformance suite rejects coercion explicitly.
- **Forgetting the deprecated-values rule.** Read paths must accept
  any stored enum value indefinitely; write paths MAY reject
  deprecated values.
- **Forgetting `submit.configure(storage=...)` at startup.** The
  router has no default storage backend (because the choice is the
  consumer's call); without `configure()` the endpoints 500.
- **Calling async storage methods (`storage.save_report(...)`) without
  awaiting them.** The routers handle this; only matters if you reach
  into storage directly. Wrap with `asyncio.run(...)` from sync code.
- **Using `window.BugFabConfig = {...}`.** There is no such global.
  Configure the bundle by calling `window.BugFab.init({...})` after
  the script loads.
- **Adding consumer-specific code (auth checks, business logic)
  inside Bug-Fab routers.** Bug-Fab is generic; put consumer logic in
  consumer middleware. Patches that consumer-specialize the routers
  will be rejected.
- **Passing `directory=...` to `FileStorage`.** The keyword is
  `storage_dir=...` (matches the env var). Older drafts and some
  third-party docs use `directory=...` — that is wrong.
- **Mounting the submit router under `/api/bug-reports`.** The router
  itself owns the `/bug-reports` suffix; mount under `/api` to get
  the canonical `/api/bug-reports` endpoint.
- **Defaulting JSON to `camelCase`.** The wire protocol is `snake_case`
  everywhere. .NET and Drizzle/TypeScript stacks default to camelCase
  and need explicit overrides.

---

## Quick orientation for AIs editing Bug-Fab itself

If you are not integrating Bug-Fab but contributing to it, also read
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md). Three things to know
up front:

- **Pre-commit `forbidden-strings` hook.** A list of private project
  identifiers is checked into `.pre-commit-forbidden-strings.txt`
  (one term per line, comments with `#`). Any commit whose staged
  content contains a match is blocked. If your commit fails this
  hook, double-check what you are staging — usually it is a lifted
  comment or sample log line that snuck through. Do not edit the
  forbidden-strings list to make your commit go through; fix the
  content.
- **Conformance suite runs on every PR.** Any change to the protocol
  schemas, routers, or storage layer must keep the suite green.
  Local: `pytest -m conformance`.
- **v0.1 scope is frozen.** New features land in v0.2 and beyond. The
  in-scope set for v0.1 is in [`docs/ROADMAP.md`](docs/ROADMAP.md).
  Surprise PRs that expand scope tend to need significant rework
  before merging — please open an issue first to align.
