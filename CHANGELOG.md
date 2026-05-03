# Changelog

All notable changes to Bug-Fab are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While Bug-Fab is on `0.x`, minor version bumps may include breaking
changes per the semver pre-1.0 convention. Breaking changes are called
out explicitly in each release entry.

## [Unreleased]

### Added

- Generic webhook delivery — `bug_fab.integrations.webhook.WebhookSync`
  (FastAPI / Flask) plus `bug_fab.adapters.django.webhook_sync.send`
  (Django sync flavor) best-effort `POST`s every successfully persisted
  bug report as JSON to a consumer-configured URL. Targets Slack
  incoming-webhooks, Linear project webhooks, Pushover, n8n / Zapier
  triggers, custom collectors — anything that accepts a JSON body.
  Configurable via four `BUG_FAB_WEBHOOK_*` settings (enabled, URL,
  headers, timeout); `BUG_FAB_WEBHOOK_HEADERS` accepts both JSON-object
  and `key=value;key2=value2` formats. Wired into all three adapters
  after the GitHub Issues sync so any populated `github_issue_url`
  rides along in the outbound payload. Same failure-tolerance
  contract as the GitHub sync — non-2xx responses, timeouts, and
  transport errors all log at `WARNING` and never block the intake
  201 response. 30 new tests covering happy path, headers, all three
  failure modes, off-by-default behavior, and adapter wiring across
  FastAPI / Flask / Django. See `docs/DEPLOYMENT_OPTIONS.md` §
  "Webhook delivery" for the full recipe.
- Configurable FAB position. `BugFab.init({ position })` accepts one of
  `"bottom-right"` (default, back-compat), `"bottom-left"`, `"top-right"`,
  `"top-left"`, or a free-form `{ top, bottom, left, right }` object.
  Resolves into inline styles at FAB-element creation time so callers can
  drop the FAB anywhere on the viewport without overriding the bundle's
  CSS. (FAB UX TH-5.)
- Anchor-to-element mode for the FAB. New init options `stackAbove`,
  `stackBelow`, `stackLeft`, `stackRight` accept a CSS selector or an
  `HTMLElement` and position the FAB adjacent to that anchor with a
  configurable `gap` (default 12px). The position is recomputed on
  `window.resize`, when an `IntersectionObserver` fires for the anchor,
  and when a `MutationObserver` sees `class`/`style` changes on the
  anchor — so theme switches and layout reflows do not strand the FAB.
  Falls back to the configured `position` (and logs a console warning)
  when the anchor selector does not resolve. (FAB UX TH-6.)
- Public `BugFab.disable()` and `BugFab.enable()` runtime API. `disable()`
  hides the FAB (toggles a `bug-fab--hidden` class) and closes the
  overlay if it was mid-edit; `enable()` re-shows the FAB and lazily
  creates it when init() ran while disabled. The bundle's own `<script>`
  tag also honors `data-bug-fab-disabled="true"` so non-JS templates can
  flip the kill-switch without rebuilding the init config. The
  `enabled` init option is now documented to accept boolean OR
  `() => boolean`. (FAB UX TH-7.)
- Per-report category dropdown. When `BugFab.init({ categories: [...] })`
  is set, the report form renders a `<select>` between the title and
  description fields with the supplied options, labeled per
  `categoryLabel` (default `"Category"`). The chosen value is prepended
  to the existing `tags` array on submit, riding the wire protocol's
  existing `tags: string[]` field — no protocol change. When
  `categories` is unset (default), the form looks identical to today.
  (FAB UX TH-15.)
- First-party Flask adapter at `bug_fab.adapters.flask.make_blueprint(settings)`.
  Returns a Flask Blueprint exposing the full v0.1 wire protocol (all 8
  endpoints + HTML viewer + static bundle). Install with
  `pip install bug-fab[flask]`. A Flask consumer's integration code drops
  from ~250 LOC to ~10 LOC. Reuses `bug_fab.intake.validate_payload()` so
  the adapter is protocol-conformant by construction; reuses the same
  Jinja2 templates and static bundle the FastAPI router serves. GitHub
  Issues sync wired on intake + status update (best-effort, mirrors the
  FastAPI router). 24 integration tests covering all 8 endpoints +
  lifecycle + bulk + HTML viewer + GitHub-sync wiring +
  GitHub-failure-still-persists path.
- First-party Django reusable app at `bug_fab.adapters.django` —
  `pip install bug-fab[django]`, add to `INSTALLED_APPS`, run
  `manage.py migrate`, mount the intake + viewer URLconfs. Native
  Django ORM models (`BugReport` + `BugReportLifecycle`), a free
  `BugReportAdmin` for the admin UI, plain Django views (no DRF
  dependency), and a `LoginRequiredMixin`-based auth helper.
  Validation flows through `bug_fab.intake.validate_payload` so the
  wire-protocol contract is shared with the FastAPI reference.
  28 integration tests + 29/29 conformance suite passing against a
  live `runserver`. Example app at `examples/django-minimal/`.
- `Settings.csp_nonce_provider` callable hook — when set, the viewer
  stamps the returned per-request nonce onto every inline `<script>`
  tag in `list.html`, `detail.html`, and `_base.html`. Lets consumers
  adopt a strict `Content-Security-Policy` (no `'unsafe-inline'` for
  `script-src`) without forking the package. See
  [`docs/CSP.md`](docs/CSP.md) for the FastAPI middleware recipe.
  Default is `None`, preserving existing rendering behavior.
- `docs/DEPLOYMENT_OPTIONS.md` § "Upgrading between Bug-Fab versions"
  — recipe for running the bundled `bug_fab/storage/_alembic/`
  migrations from a consumer project, plus the design for per-version
  `_migrate.py` scripts under `FileStorage` (committed for v0.2). A
  consumer-input audit on 2026-05-03 surfaced the absence of this as
  a v1.0 must-have. (TH-10.)
- `docs/DEPLOYMENT_OPTIONS.md` § "Auth recipes" — copy-paste-able
  snippets for HTTP Basic, cookie-session, and OAuth2 / JWT bearer
  (all FastAPI), plus pointers to `flask-login` and Django
  `LoginRequiredMixin` for the other framework adapters. Recipes,
  not new code — they wire into the existing mount-point auth
  pattern. (TH-17.)
- `examples/fastapi-jinja-docker/` — richer reference example with
  Jinja2 templates, `SQLiteStorage`, multi-stage `Dockerfile`, and a
  `docker-compose.yml` mounting `./data` for persistence. The
  `<script>` tag lives in `base.html` so every page extending the
  base template gets the FAB. (TH-18.)
- `INTEGRATION_AGENTS.md` (sibling of `AGENTS.md`) tailored to AI
  coding sessions adding Bug-Fab to a host FastAPI / Flask / Django
  app. TL;DR + required-reading list + the four 2026-05-03 audit
  findings as a "things you'd hit if you skipped the docs" reference
  + standard wiring snippet + scope-check prompt for the user.
  (TH-19.)

### Changed

- Replaced the inline `onclick="window.location.reload()"` on the
  list-view Refresh button with a `data-bug-fab-action="reload"`
  attribute and a single `addEventListener` registration in the
  page's existing `<script>` block. Strict CSP forbids inline event
  handlers; this keeps the same UX while letting the page render
  cleanly under `script-src 'nonce-...'` without `'unsafe-inline'`.
- Tightened the FastAPI intake router's image-format validation to match
  PROTOCOL.md v0.1: `POST /bug-reports` now accepts only `image/png`
  screenshots and rejects JPEG (and every other format) with `415
  Unsupported Media Type`. The bundled `html2canvas` client only emits
  PNG, the protocol-validation library `bug_fab.intake` already enforced
  PNG-only, and the viewer's `GET /reports/{id}/screenshot` always
  returned `image/png`; the router was the lone outlier and silently
  accepted JPEG bytes that were then served back with the wrong
  Content-Type. Not a breaking protocol change — the spec and JSON
  Schema have always been PNG-only.

### Deprecated

### Removed

### Fixed

- Resolved the drift between the intake router (which accepted both PNG
  and JPEG) and the viewer screenshot endpoint (which always served
  `image/png`). Stored screenshots are now guaranteed to match the
  served Content-Type because intake rejects non-PNG bytes by magic
  signature.
- Flask adapter audit (F-3): `abort(404)` and `abort(403)` calls inside
  blueprint handlers returned Flask's default HTML error pages instead
  of the protocol's `{error, detail}` JSON envelope. Registered
  `@bp.errorhandler(404)` and `@bp.errorhandler(403)` so every non-2xx
  body matches the documented envelope per `PROTOCOL.md` § Error
  responses.
- Flask adapter audit (F-1): GitHub Issues sync was entirely missing
  from `bug_fab.adapters.flask` — silent feature regression for any
  consumer with `github_enabled=True`. Wired `create_issue` after
  intake save and `sync_issue_state` after status update, both
  best-effort with try/except logging mirroring the FastAPI router.

### Security

- Document the CSP-nonce integration path
  ([`docs/CSP.md`](docs/CSP.md)) so consumers running strict CSP have
  a first-class hook into the viewer's inline scripts instead of
  needing to whitelist `'unsafe-inline'` or fork templates.

## [0.1.0a1] - 2026-04-27

Initial alpha release. Reserves the `bug-fab` name on PyPI and validates
the publish workflow end-to-end before the `v0.1.0` final release.

`pip install bug-fab` skips alphas by default; install with
`pip install --pre bug-fab` to try this version.

### Added

- Project scaffolding: `pyproject.toml` (PEP 621, Hatchling backend),
  ruff lint and format configuration, pytest configuration with
  coverage gating at 85%.
- Optional dependency extras: `bug-fab[sqlite]` and `bug-fab[postgres]`
  for SQL storage backends via SQLAlchemy and Alembic.
- Pytest plugin entry-point `bug-fab-conformance` reserved for the
  protocol conformance suite consumed by adapter authors.
- Pre-commit configuration with forbidden-strings, ruff, and standard
  hygiene hooks.
- Editor and git metadata: `.editorconfig`, `.gitattributes` enforcing
  LF line endings and treating vendored JS as binary.
- GitHub Actions CI: matrix testing on Python 3.10 / 3.11 / 3.12,
  ruff lint and format checks, coverage gate, wheel and sdist build
  with `twine check`, Trusted Publishing to PyPI on `v*` tags.

### Notes

- This release exists primarily to claim the `bug-fab` PyPI name and
  exercise the build/publish pipeline. The package surface itself is
  intentionally minimal; the full v0.1 feature set lands in `0.1.0`.
- Wire-protocol contract is not yet locked. Do not build production
  integrations against this alpha.

[Unreleased]: https://github.com/AZgeekster/Bug-Fab/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/AZgeekster/Bug-Fab/releases/tag/v0.1.0a1
