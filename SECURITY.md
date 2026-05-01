# Security Policy

Bug-Fab is a personal open-source project maintained by one person in
their spare time. Security reports are still very welcome — this page
explains how to file one and what to expect back.

## Reporting a vulnerability

**Channel:** open a private security advisory on GitHub.

- https://github.com/AZgeekster/Bug-Fab/security/advisories/new

GitHub's private vulnerability advisories let us discuss a fix in
private before any details are made public, request a CVE if the issue
warrants one, and publish a coordinated disclosure when a patched
release is ready. There is no separate email contact — the advisory
form is the one supported channel.

When you file a report, the most useful things to include are:

- Affected version (e.g., `0.1.0`, commit SHA, or "main as of
  YYYY-MM-DD").
- A short description of the issue and the impact you observed.
- Steps to reproduce, including any minimal config or payload.
- Whether the issue is already public (e.g., disclosed in another
  advisory, posted on a forum, etc.).

Please **do not** open a regular GitHub Issue for a security report —
that puts the details in public before a fix is available.

## Response expectations

Bug-Fab is **best-effort, hobbyist OSS**. The targets below are what
the maintainer aims for, not a contractual SLA:

| Stage | Target |
|---|---|
| Acknowledge the report | Within 7 days |
| Assess severity and confirm/deny | Within 14 days |
| Ship a fix for high or critical severity | Within 30 days |
| Ship a fix for low severity | Best effort; may be folded into the next regular release |

If you have heard nothing after 14 days, a polite nudge on the
advisory thread is welcome — the maintainer probably missed the
notification.

Coordinated disclosure is preferred: please give the maintainer a
reasonable window to publish a fix before going public. If you are
working to a disclosure deadline (e.g., a 90-day clock), say so in
your initial report so it can be planned around.

## Supported versions

Only the latest released version receives security fixes. Once
`v0.1.0` ships, the table below will track which lines are still
in scope.

| Version | Status | Security fixes |
|---|---|---|
| `0.1.0a1` | Alpha — not for production | No |

`0.1.0a1` exists to reserve the PyPI name and validate the publish
workflow. Do not deploy it. Track [`v0.1.0`](https://github.com/AZgeekster/Bug-Fab/milestones)
for the first supported release.

## Threat model summary (v0.1)

Bug-Fab v0.1 is a small surface: a multipart intake endpoint, a JSON
viewer, a vanilla-JS frontend, and three storage backends. The
sections below describe what the package **does** protect against and
what it **does not** — both matter for deciding how to deploy it.

### What v0.1 does protect against

- **Schema validation on intake.** `POST /bug-reports` rejects
  malformed multipart, missing required parts, wrong types, and
  unknown enum values. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for
  the full schema.
- **Strict severity and status enums.** Adapters MUST reject unknown
  values with `422`. Silent coercion fails conformance.
- **Magic-byte PNG check on the screenshot part.** A request that
  claims `image/png` but does not start with the PNG magic bytes is
  rejected — the screenshot is not blindly written to disk.
- **Atomic storage writes.** `FileStorage` writes via tmp + rename so
  a crash mid-write cannot leave a half-written `metadata.json` or
  `screenshot.png` for the viewer to render.
- **Server-captured `User-Agent` as the source of truth.** The
  request-header `User-Agent` is captured independently of the
  client-supplied `client_reported_user_agent`. The client value is
  preserved separately for diagnostics but is never trusted as
  authoritative. See [`PROTOCOL.md` §
  User-Agent trust boundary](docs/PROTOCOL.md#user-agent-trust-boundary).
- **Best-effort GitHub sync.** A GitHub outage cannot fail an
  otherwise-valid bug submission — sync errors log server-side and
  return `github_issue_url: null`.

### What v0.1 does NOT protect against

These are the deliberate limits of v0.1, not bugs. Each is a known
gap that consumers must address themselves until the corresponding
roadmap item lands. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

- **Authentication.** Bug-Fab v0.1 ships no auth abstraction. Both
  intake and viewer routes are unauthenticated by default; consumers
  protect them by mounting each router behind their existing
  framework auth middleware. The `AuthAdapter` ABC lands in v0.2.
- **Authorization / per-user permissions.** The `viewer_permissions`
  config gates which **endpoints** are mounted, not which **users**
  may call them. Per-user gating arrives with `AuthAdapter` in v0.2.
- **Per-user rate limiting.** Only per-IP rate limiting is available
  in v0.1 (off by default). Per-IP is bypassable via NAT, VPN, and
  shared offices — treat it as accidental-flood protection rather
  than user accountability.
- **Automatic PII redaction in the error / network buffers.** The
  buffers carry whatever the consumer's app emits to `console.error`,
  `console.warn`, and `fetch` / `XHR` URLs. Bug-Fab does not scrub
  them.
- **Automatic password / token redaction in screenshots.** The
  screenshot is what `html2canvas` captures from the live DOM — if a
  password field is visible, it is in the PNG.
- **Encryption at rest.** Stored screenshots and metadata sit on the
  consumer's filesystem (or in their database) in plaintext. Disk-
  level encryption is the consumer's responsibility.
- **Audit logging beyond the lifecycle field.** The `lifecycle` array
  on each report records status changes, but there is no separate
  audit log for views, downloads, or list queries. Audit-on-view
  arrives with `AuthAdapter` in v0.2.
- **Browser session protection beyond what the consumer's framework
  already provides.** Bug-Fab does not set CSRF tokens, configure
  cookies, or enforce origin checks — those belong to the host app.

### Known security considerations consumers should handle

These are things every Bug-Fab deployment needs to think about
explicitly. The package will not protect you from any of them.

- **The error and network buffers can contain whatever your app
  emits.** Auth tokens in URL query strings show up in the network
  log. Logged session secrets show up in the console buffer. The
  right place to fix this is in your app — do not log tokens, do not
  put them in query strings, do not render them to the DOM.
- **Screenshots can contain anything visible in the browser.** A user
  with a password manager open in another tab is fine; a user with a
  reset-token URL on screen is not. There is no "blur sensitive
  fields" pass in v0.1.
- **The viewer endpoints expose every submitted report to anyone with
  access to the mounted URL prefix.** Auth gating is the consumer's
  job. The most common mistake is mounting the viewer at a public
  URL because mount-point auth was never added. See
  [`docs/DEPLOYMENT_OPTIONS.md` § Router mount-point auth pattern](docs/DEPLOYMENT_OPTIONS.md#router-mount-point-auth-pattern).
- **Screenshot files are not web-public by default**, but they
  *become* exposed if you serve the storage directory as static
  content. Don't.
- **The submit endpoint is unauthenticated by default.** If your
  consumer mounts it on the public internet without rate limiting,
  enable per-IP rate limiting (`BUG_FAB_RATE_LIMIT_ENABLED=true`).
  The public POC on Fly.io runs with rate limiting on for exactly
  this reason.
- **GitHub Personal Access Tokens used for issue sync are sensitive.**
  They live in environment variables. Rotate on any incident.

### Cryptographic dependencies

Bug-Fab v0.1 has **no direct cryptographic dependencies**. Transport
security relies on the consumer's TLS configuration — Bug-Fab speaks
plaintext HTTP and the consumer's framework / reverse proxy is
expected to terminate TLS in any non-localhost deployment.

The vendored `html2canvas.min.js` is pinned at v1.4.1 inside
`static/vendor/`. If upstream `html2canvas` ships a security patch,
that pin should be rotated and a patch release of Bug-Fab cut. The
maintainer watches the `html2canvas` repo for releases; if you spot
a relevant CVE first, please file a security advisory via the
channel above.

## Once you have reported

The maintainer will:

1. Acknowledge receipt of the report.
2. Confirm whether the issue is in scope and reproducible.
3. Discuss severity, impact, and a fix plan on the advisory thread.
4. Ship a patched release.
5. Publish the advisory (with credit to the reporter, unless you
   prefer to remain anonymous) once consumers have had a reasonable
   window to upgrade.

Thank you for taking the time to report responsibly.

## Stored-XSS audit

The viewer renders user-submitted strings into two HTML pages —
`bug_fab/templates/list.html` and `bug_fab/templates/detail.html`,
both extending `bug_fab/templates/_base.html`. Templates are loaded
through `fastapi.templating.Jinja2Templates`, which constructs a Jinja2
`Environment` with autoescape enabled for `.html`, `.htm`, and `.xml`
files by default. Bug-Fab does not override that default, does not
apply the `|safe` filter to any user-controlled field, and never wraps
user input in `markupsafe.Markup(...)` before passing it to the
template. Every interpolation below therefore goes through Jinja2's
HTML autoescape pass.

### User-controlled fields

These fields originate in the client bundle and ride the multipart
intake into storage. The viewer renders them as escaped text:

- `report.title` — rendered in `list.html` (table row) and
  `detail.html` (page heading); HTML-escaped.
- `report.description` — rendered in `detail.html` inside a `<p>`;
  HTML-escaped.
- `report.expected_behavior` — rendered in `detail.html` inside a
  `<p>` when present; HTML-escaped.
- `report.module` — rendered in both `list.html` and `detail.html`;
  HTML-escaped.
- `report.environment` — rendered in `detail.html`; HTML-escaped.
- `report.tags` (list of strings) — each tag rendered as an `<li>` in
  `detail.html`; HTML-escaped per element.
- `report.context.url` — rendered in `detail.html` both as link text
  and as an `href` attribute, and as a `Reproduce` button `href` in
  the page header; HTML-escaped (attribute context). The link gets
  `target="_blank" rel="noopener"` so a malicious URL cannot reach
  back into the viewer's `window.opener`. The URL itself is not
  scheme-validated in v0.1 — a `javascript:` URL would be rendered as
  an inert escaped attribute by Jinja2 but a viewer who clicks it
  would still navigate; consumers who expose the viewer to untrusted
  submitters should add a scheme allowlist in front of this.
- `report.context.user_agent` (client-reported) — rendered in
  `detail.html` inside `<span class="bug-fab-mono">`; HTML-escaped.
- `report.context.console_errors[].message` and `.level` — rendered
  in `detail.html` inside a `<pre>` block; HTML-escaped.
- `report.context.network_log[].method`, `.url`, `.status`,
  `.duration_ms` — rendered in `detail.html` inside a `<pre>` block;
  HTML-escaped.
- `report.context.source_mapping` (key/value strings) — rendered in
  `detail.html` inside a definition list; HTML-escaped on both key
  and value.
- `report.lifecycle[].by`, `.fix_commit`, `.fix_description` —
  rendered in `detail.html` inside the lifecycle table; HTML-escaped.
  `by` and `fix_*` fields are populated by status-update callers and
  are therefore considered semi-trusted (they originate from a viewer
  user or an adapter), but they ride the same escape path either way.
- `report.id` — passed to the detail-page client-side script via
  `report.id|tojson`. The `tojson` filter produces a safe JSON literal
  that Jinja2 still autoescapes for HTML context, which guarantees the
  embedded string cannot break out of the surrounding `<script>` block.
  Storage validates `report.id` against the `bug-NNN` shape regex
  before lookup, so even a hostile id is constrained to that
  character class.

### Server-controlled fields

These fields are filled in by the FastAPI router or the storage layer
and are not user-supplied. They are still autoescaped, but they are
not part of the XSS attack surface:

- `report.created_at`, `report.updated_at` — ISO timestamps from the
  server clock.
- `report.status`, `report.severity`, `report.report_type` — strict
  enum values rejected with 422 on intake if they are not in the
  allowed set.
- `report.server_user_agent` — the request-header `User-Agent`
  captured by the intake router (the trusted counterpart to the
  client-reported value).
- `report.github_issue_url` — built by the GitHub Issues integration
  from `Settings.github_repo` and the issue number, neither of which
  is user-supplied.
- `report.lifecycle[].action`, `.at` — server-generated state
  transitions and timestamps.
- Stat-card counts (`stats.open`, `stats.fixed`, etc.) and pagination
  state (`page`, `total_pages`) — integers from the storage layer.
- Permission flags (`permissions.can_edit_status`, `.can_delete`,
  `.can_bulk`) — booleans from `Settings.viewer_permissions`.

### Audit conclusion

We walked every Jinja2 expression in `list.html`, `detail.html`, and
`_base.html`. Autoescape is on at the environment level, no template
applies `|safe` to a user-controlled field, no expression is wrapped
in `Markup(...)` before reaching the template, and the one place we
embed a user-controlled value into a `<script>` block (`report.id`)
goes through `|tojson` after the storage layer has already constrained
it to a regex-safe shape.

**No stored-XSS sinks were found in the v0.1 viewer.** The one
residual sharp edge is `report.context.url` rendered as an `href` —
the value is HTML-attribute-escaped, but its scheme is not validated.
Consumers exposing the viewer to untrusted submitters should layer a
scheme allowlist in front of the viewer or scrub `context.url` on
intake. This is tracked as a roadmap item rather than a v0.1 bug
because the viewer is unauthenticated by default and the
deploy-time threat model already assumes the viewer is gated behind
the consumer's auth perimeter.

## CSRF guidance for the intake endpoint

`POST /api/bug-reports` accepts a multipart payload from the
`bug-fab.js` bundle running in the user's browser. Bug-Fab v0.1 does
not require, validate, or set a CSRF token on this endpoint, and it
does not enforce an `Origin` / `Referer` allowlist. This is a
deliberate v0.1 design choice driven by the embed and extension use
cases the protocol is meant to support: a browser extension or a
third-party-embedded widget legitimately POSTs from a different origin
than the host app, and a CSRF-token requirement would break those
deployments without adding meaningful protection (the extension can
read the token if it can read the DOM).

The threat model assumption baked into v0.1 is therefore: **intake is
cross-origin-tolerable; the consumer's deployment topology is what
decides whether to lock it down.** A bug-report submission carries a
screenshot and free-text fields — the worst-case CSRF outcome is a
flood of unsolicited reports, which is what the per-IP rate limiter
(`BUG_FAB_RATE_LIMIT_ENABLED=true`) and the storage size cap
(`BUG_FAB_MAX_UPLOAD_MB`) exist to bound. There is no state-changing
side effect on the host app's session, so a forged POST from a
malicious page cannot escalate beyond "submit a bug report on the
victim's behalf."

Consumers who run the **viewer and the intake on the same auth
perimeter** — e.g., both mounted under `/admin/`, both gated by the
same session cookie — should treat that perimeter the same way they
treat any other authenticated endpoint and add the framework-level
defenses they would normally add:

- Set `SameSite=Lax` (or `Strict`) on the session cookie so a
  cross-origin POST does not carry credentials. This is the single
  highest-leverage mitigation for forged submissions; modern browsers
  default to `Lax` for new cookies, but explicit is better.
- Add an `Origin` (or `Referer`) header check in middleware in front
  of `submit_router` that rejects requests whose `Origin` is not in
  an allowlist of trusted hosts.
- If the intake must accept a true cross-origin POST (e.g., a
  reporting form on a marketing site that targets a separate API
  host), serve it on a dedicated unauthenticated subdomain so the
  session cookie is not in scope.

A concrete sketch for the FastAPI reference, dropped in front of
`submit_router`:

```python
from fastapi import FastAPI, Request, HTTPException

ALLOWED_ORIGINS = {"https://app.example.com"}

@app.middleware("http")
async def origin_allowlist(request: Request, call_next):
    if request.url.path.startswith("/api/bug-reports") and request.method == "POST":
        origin = request.headers.get("origin", "")
        if origin and origin not in ALLOWED_ORIGINS:
            raise HTTPException(status_code=403, detail="origin not allowed")
    return await call_next(request)
```

A formal `CSRFAdapter` (token issuance + verification on the same
session as the host app) is a v0.2 candidate; the design intent is to
keep it opt-in so the embed and extension cases continue to work
without ceremony.

## Supply-chain

### Runtime dependencies

These are the five packages declared in `[project.dependencies]` of
`pyproject.toml`. Each is what `pip install bug-fab` pulls in:

- **`fastapi>=0.110`** — the HTTP framework the reference adapter is
  built on. Provides routing, dependency injection, and OpenAPI
  schema generation. Lower bound only; the upper bound floats so
  consumers can adopt new FastAPI releases without waiting on a
  Bug-Fab release.
- **`pydantic>=2.0`** — the data-validation layer behind every wire
  schema (`BugReportCreate`, `BugReportDetail`, `BugReportStatusUpdate`,
  etc.). Pydantic 2.x is required because the schemas use
  `model_validate` / `model_dump` semantics; v1.x is not supported.
- **`python-multipart`** — required by FastAPI for multipart form
  parsing on `POST /bug-reports`. Bug-Fab does not import it directly;
  it is listed because FastAPI's `Form` / `File` parameters silently
  fail without it. Floating version: whatever FastAPI's compatibility
  matrix accepts.
- **`httpx`** — the async HTTP client used by the GitHub Issues
  integration (`bug_fab/integrations/github.py`). Floating version.
- **`jinja2>=3.0`** — the template engine for the viewer's
  `list.html` and `detail.html` pages. v3.x is the minimum because
  the `Jinja2Templates(directory=...)` call in `viewer.py` relies on
  the autoescape default that v3 ships with.

### Optional extras

- **`[sqlite]`** — `sqlalchemy>=2.0`, `alembic`. Pulls SQLAlchemy 2.x
  for the SQLite storage backend and Alembic for the bundled
  migration. Both have lower bounds because the backend code uses
  SQLAlchemy 2.x async syntax.
- **`[postgres]`** — `sqlalchemy>=2.0`, `alembic`,
  `psycopg[binary]`. Same as `[sqlite]` plus the binary build of
  psycopg 3 for Postgres. The binary build avoids requiring the
  consumer to install libpq separately on most platforms.
- **`[dev]`** — `pytest>=7.0`, `pytest-cov>=4.0`, `ruff>=0.5`,
  `pre-commit>=3.0`, `build`, `twine`. The maintainer's local toolchain;
  not installed by `pip install bug-fab`.
- **`[e2e]`** — `playwright>=1.40`, `pytest-playwright>=0.4`,
  `uvicorn[standard]`. End-to-end browser tests run against a live
  uvicorn process; not in the runtime install.

### Vendored

`static/vendor/html2canvas.min.js` is a verbatim copy of
`html2canvas` v1.4.1, MIT-licensed by Niklas von Hertzen. Its license
header is preserved as the leading comment block of the minified file
(`Copyright (c) 2022 Niklas von Hertzen ... Released under MIT
License`), as is the embedded Microsoft helper notice further down.
We do not ship a separate `LICENSE` file inside `static/vendor/`
because the license text rides inside the bundle itself; if you
redistribute `bug-fab.js` and the vendor file together, that header
travels with it.

`html2canvas` is vendored rather than declared as an npm dependency
for three reasons. First, Bug-Fab v0.1 does not publish to npm — the
frontend ships as a copy of the `static/` directory inside the
Python wheel, so consumers do not run a JavaScript build step.
Second, `html2canvas` is the only browser dependency, and pinning a
single 194 KB file in the repo is simpler than wiring a JS toolchain
for one library. Third, the pin lets us audit a single known-good
version against any future security advisories rather than tracking
whatever floating range an npm install would resolve to. When
upstream `html2canvas` ships a security patch, the maintainer rotates
the pin and cuts a Bug-Fab patch release.

### Pinning policy

Runtime dependencies in `pyproject.toml` use **lower bounds only**.
The package itself never ships a lockfile and never pins to an exact
version. This keeps Bug-Fab compatible with whatever FastAPI /
Pydantic / Jinja2 release the consumer's app already runs against,
which matters because Bug-Fab is meant to drop into an existing app
without forcing a coordinated dependency upgrade. Lockfiles
(`requirements.txt`, `uv.lock`, `poetry.lock`) are the consumer's
responsibility — that is where the transitive dependency graph
should be frozen for reproducible deploys. Bug-Fab's contract is
"these lower bounds are what we test against; anything newer in the
same major series is expected to work."

The vendored `html2canvas.min.js` is the one exception: it is pinned
to v1.4.1 because the frontend bundle does not have a
dependency-resolution layer to float against.

### Reporting a supply-chain issue

If you find a security issue in a Bug-Fab dependency or in the
vendored `html2canvas` build that affects Bug-Fab consumers, please
file it through the same private advisory channel linked at the top
of this document.
