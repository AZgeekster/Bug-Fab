# Changelog

All notable changes to Bug-Fab are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While Bug-Fab is on `0.x`, minor version bumps may include breaking
changes per the semver pre-1.0 convention. Breaking changes are called
out explicitly in each release entry.

## [Unreleased]

### Added

- **Four new outbound-integration adapters** under `bug_fab/integrations/`,
  mirroring the shape `SlackSync` established earlier today
  (`.send(report) -> bool` + `.from_env()` + same best-effort failure-
  tolerance contract — failures log at WARN and never raise into the
  intake response path):
  - **`discord.py` — `DiscordSync`** (282 LOC, 25 tests). Builds a
    Discord webhook payload using the embed shape: severity-mapped
    integer color sidebar, four inline fields (reporter / status /
    environment / module), title click-through to the viewer URL when
    configured. Env: `BUG_FAB_DISCORD_{ENABLED,WEBHOOK_URL,
    VIEWER_BASE_URL,TIMEOUT_SECONDS,USERNAME}`. Treats Discord's
    `204 No Content` (its success code) as success alongside 2xx.
  - **`teams.py` — `TeamsSync`** (322 LOC, 26 tests). Microsoft Teams
    incoming-webhook via Adaptive Cards schema v1.4: severity-mapped
    color names (`attention` / `warning` / `accent` / `good` /
    `default`), `FactSet` for reporter/status/env/module fields with
    empty fields elided, `Action.OpenUrl` button when a viewer URL is
    configured (entire `actions` array omitted otherwise — some Teams
    clients reject empty actions). Env:
    `BUG_FAB_TEAMS_{ENABLED,WEBHOOK_URL,VIEWER_BASE_URL,TIMEOUT_SECONDS}`.
  - **`linear.py` — `LinearSync`** (350 LOC, 35 tests). Linear issue
    creation via GraphQL — mirrors `GitHubSync.create_issue` shape
    (`create_issue(report) -> (identifier, url) | (None, None)`)
    rather than the webhook shape, because Linear is an issue tracker
    not a chat channel. Severity → priority (critical=1 / high=2 /
    medium=3 / low=4 / unknown=0). Captures three Linear-specific
    quirks: no `Bearer` prefix on `Authorization`, 200-with-`errors[]`
    GraphQL convention (status code alone is insufficient — check the
    envelope for `errors[]` AND `data.issueCreate.success: false`),
    return the human-facing `identifier` (`BUG-42`) not the internal
    UUID. Env: `BUG_FAB_LINEAR_{ENABLED,API_KEY,TEAM_ID,API_URL,
    VIEWER_BASE_URL,DEFAULT_LABEL_IDS,TIMEOUT_SECONDS}`.
  - **`pagerduty.py` — `PagerDutySync`** (369 LOC, 36 tests). Events
    API v2 (`https://events.pagerduty.com/v2/enqueue`) with
    **critical-by-default escalation**: non-critical severities return
    `False` without a network call and log at DEBUG, so a default
    deployment doesn't page on-call for every low-severity report.
    Configurable via the `escalate_severities` tuple or
    `BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES` (comma-separated).
    Severity → PagerDuty severity (critical=`critical` / high=`error`
    / medium=`warning` / low=`info`). `dedup_key` = `bug-fab-<id>` so
    a retried webhook collapses into one incident. Env:
    `BUG_FAB_PAGERDUTY_{ENABLED,INTEGRATION_KEY,ESCALATE_SEVERITIES,
    API_URL,VIEWER_BASE_URL,TIMEOUT_SECONDS,DEDUP_PREFIX}`.

  All four use raw `httpx` (no SDK deps). Slot through the existing
  `webhook_sync` slot of `submit.configure()` (or `github_sync` for
  Linear) just like Slack — no router changes. 122 new tests total.
- **Six more first-party adapters** promoted 2026-05-21 — every pre-session draft in `notes/adapter_drafts/` was verified end-to-end under Docker, polished (draft-marker rewrites, missing `MIGRATION_NOTES.md` authored from scratch where absent, `.gitignore` added, build artifacts scrubbed), and copied to `repo/adapters/`:
  - **Express + TypeScript** at `repo/adapters/express/`. `createBugFabRouter(opts)` factory returns a mountable `express.Router`; default `FileStorage` + 9-method `IStorage` interface for custom backends; multer `memoryStorage` so the PNG magic-byte check runs before persistence. 41/41 vitest passing under `docker run --rm node:20`. Replaces the previous 🟡 sketch entry.
  - **Hono + TypeScript** at `repo/adapters/hono/`. Single `createBugFabApp({ storage })` runs unchanged on Node, Bun, Deno, Cloudflare Workers, and Vercel Edge; three Web-standard storage backends (Memory, R2, Workers KV) with no `node:fs` / `Buffer`. 44/44 vitest passing — fixed two bugs during promotion: a trailing-slash routing miss on the mounted sub-app (Hono v4's `route()` collapses `/` + `''` paths; now explicit on the parent) and a missing global `notFound` handler that was returning Hono's default plain text instead of the protocol's `{error, detail}` envelope.
  - **SvelteKit + TypeScript** at `repo/adapters/sveltekit/`. One `+server.ts` per protocol endpoint (8 files in `examples/route-tree/`); two storage backends (`FileStorage` for `adapter-node` single-process, `DrizzleStorage` with pluggable `screenshotIO` for R2/S3/BYTEA). Svelte pin bumped from `^4.2.12` to `^5.0.0` (the `<BugFab />` component was already written to the v4/v5 overlap). 35 passed + 1 skipped under `docker run --rm node:20`.
  - **Spring Boot + Kotlin** at `repo/adapters/spring-boot-kotlin/`. Two storage backends (`file` default, `jpa` via Spring Data JPA). 24/24 passing under `docker run --rm gradle:8-jdk17`. Promotion uncovered three real adapter bugs that have been fixed: (a) intake controller now binds `metadata` as `MultipartFile` (was `@RequestParam String`, which silently truncated JSON-shape multipart parts the wire protocol mandates); (b) `JpaStorage` declared `open class` to satisfy CGLIB `@Transactional` proxying; (c) `HttpMessageNotReadableException` now maps to `422 schema_error` instead of Spring's default 400. Known v0.2 cleanup documented in `MIGRATION_NOTES.md`: `BugFabJpaConfiguration` package-scan footgun (workaround: consumer apps keep their `@SpringBootApplication` outside `io.bugfab.adapter`).
  - **Ruby on Rails** at `repo/adapters/rails/`. Mountable Rails Engine; ActiveRecord-only storage; consumer mounts via `mount BugFab::Engine, at: "/bug-fab"`. 22/22 minitest passing (107 assertions) under `docker run --rm ruby:3.3`. Rack 3.x status-symbol deprecations (`:unprocessable_entity`, `:payload_too_large`) flagged for v0.2 cleanup.
  - **Vapor + Swift** at `repo/adapters/vapor/`. Two storage backends (`BugFabFileStorage` + `BugFabFluentStorage` for SQLite/Postgres). 9/9 XCTest passing under `docker run --rm swift:6.0`. Required a Swift 5.10 → 6.0 strict-concurrency migration: `BugReportsController` now declares `Sendable`; XCTVapor switched to the async `{ req async throws in ... }` pattern. Promotion uncovered an error-envelope regression: `BugFabErrorMiddleware` was being prepended (`use(at: .beginning)`) to Vapor's pre-registered default `ErrorMiddleware`, which was catching errors first and emitting `{"error": true, "reason": "..."}` — fixed by replacing the middleware stack (`app.middleware = .init()`).

  Each adapter's `repo/docs/ADAPTERS_REGISTRY.md` row was flipped from 🟡 sketch / 🟡 draft / ⚫ out-of-scope to `🟢 reference (first-party adapter)`. The duplicate Tier-3 "out of scope" stubs for Laravel and Phoenix were removed (both became Tier-2 first-party adapters earlier the same day; see prior `## [Unreleased]` entry).

  This brings the first-party adapter count from 4 (FastAPI, Flask, Django, ASP.NET) → 14 by adding Go, Rust, Laravel, Phoenix, plus today's six. The wire protocol is now demonstrated across **Python, .NET, Go, Rust, Ruby, PHP, Elixir, Swift, Kotlin, and TypeScript** in 14 first-party reference adapters.
- **Phoenix (Elixir) first-party adapter** at `repo/adapters/phoenix/`.
  Promoted from private draft 2026-05-21 after a sibling-module
  reorder fix (the `BugFab.Storage.EctoStorage.BugReport` defmodule
  was being expanded before its definition lived in the file) unblocked
  the compile. 37/37 `mix test` passing under `docker run --rm
  elixir:1.16`. Mountable as a `Plug.Router` under any Phoenix
  endpoint OR as a standalone `Plug.Cowboy` app, so non-Phoenix Plug
  consumers can mount it too. Two storage backends (`BugFab.FileStorage`
  cross-readable with the Python reference, `BugFab.EctoStorage` with
  a Postgres-first migration). Minimal embedded EEx viewer; LiveView
  upgrade and Tesla-based GitHub Issues sync earmarked for v0.2.
- **Three new first-party adapters** promoted from private drafts on
  2026-05-21:
  - **Go + Gin** at `repo/adapters/go-gin/`. Module path
    `github.com/AZgeekster/Bug-Fab/adapters/go-gin`. Implements all
    eight wire-protocol endpoints with the same file-storage on-disk
    layout as the Python reference (cross-readable). 37/37
    `go test ./...` passing at 75.7% statement coverage under
    `golang:1.22`. Custom `MarshalJSON`/`UnmarshalJSON` on the
    `BugReportContext` type preserves the Pydantic `extra="allow"`
    round-trip contract. Cross-stack conformance pending a Go runner
    for the `pytest --bug-fab-conformance` harness (v0.2 candidate).
  - **Rust + Axum** at `repo/adapters/rust-axum/`. Cargo workspace
    with a `bugfab` library crate + `bugfab-example` binary. 21/21
    `cargo test --workspace` passing under `rust:1.75`. Split MSRV
    documented: 1.75 for the file-only default; the optional `sqlx`
    feature (SQLite backend) jumps the floor to 1.86 because sqlx's
    transitive `idna_adapter` / `icu_*` deps require edition 2024.
  - **Laravel + PHP** at `repo/adapters/laravel/`. Composer package
    `bugfab/laravel-adapter`, Service Provider auto-discovered.
    33/33 PHPUnit tests (89 assertions) passing under `composer:2`
    with Laravel 11.x on PHP 8.3+. Two storage backends (FileStorage
    + EloquentStorage); Octane-aware with a documented caveat about
    the default `array` cache driver multiplying per-worker
    rate-limit counters (Redis recommended for accurate per-IP
    limiting).

  Each adapter's `repo/docs/ADAPTERS_REGISTRY.md` entry was flipped to
  `🟢 reference (first-party adapter)` with conformance evidence
  captured in the registry entry.
- Opt-in PII redaction for the auto-captured buffers + free-text
  fields. New private module `bug_fab/_redact.py` defines a
  conservative three-pattern redactor: JWTs (three base64url-ish
  segments) collapse to `<redacted-jwt>`; credit card numbers that
  pass Luhn validation collapse to `****-****-****-NNNN` (last four
  preserved for chargeback correlation; non-Luhn digit groups left
  alone so transaction IDs / phone numbers don't get masked); email
  local-parts collapse so `alice@example.com` becomes
  `redacted@example.com` (the domain survives for diagnostics). The
  redactor walks a documented field set — `title`, `description`,
  `expected_behavior`, `context.console_errors[*].{message, stack}`,
  `context.network_log[*].url` — and runs inside the submit router
  *before* `storage.save_report`, so masked values are what land on
  disk. Reporter identity (`reporter.{name, email, user_id}`) is
  deliberately preserved because the consumer's UI collected it
  on purpose; consumers who don't want it captured should not pass
  it. Wired through `Settings.redact_pii` (default `False`) +
  `BUG_FAB_REDACT_PII` env var. 14 unit tests pin every pattern
  + the pure-function contract + the documented field set + the
  do-not-mask-reporter rule.
- Structured-logging hooks on the lifecycle events. A new private
  module `bug_fab/_observability.py` defines a stable event vocabulary
  (`bug_fab_report_received`, `bug_fab_status_changed`,
  `bug_fab_report_deleted`, `bug_fab_bulk_close_fixed`,
  `bug_fab_bulk_archive_closed`) and a single `emit()` helper that
  writes an `INFO`-level record on the `bug_fab.events` logger with a
  consistent `extra={"event": ..., "report_id": ..., ...}` payload.
  Wired at call sites in `bug_fab/routers/submit.py` (after a
  successful intake) and `bug_fab/routers/viewer.py` (after status
  change, delete, bulk-close-fixed, bulk-archive-closed). Consumers
  who want JSON line output for Loki / Datadog / Sentry plug in a
  standard formatter (e.g. `python-json-logger`) on the
  `bug_fab.events` logger tree — the package takes no shipper
  dependency. Suppressing the vocabulary entirely is a one-line
  `logging.getLogger('bug_fab.events').setLevel(logging.WARNING)`. 6
  unit tests pin the event-name constants, the dedicated-logger
  contract, and the `extra`-dict shape.
- Bounded retry + filesystem dead-letter queue for the generic webhook
  integration at `bug_fab/integrations/webhook.py`. `WebhookSync` gains
  three new constructor kwargs: `max_attempts` (default `1` —
  historical fire-and-forget shape preserved), `retry_backoff_seconds`
  (default `0.5`; exponential doubling per attempt), and `dlq_dir`
  (default `None`; when set, terminal failures persist as JSON
  envelopes for later replay). Retry logic classifies responses: 4xx
  is a permanent receiver-side rejection and fails fast without retry;
  5xx, timeouts, and transport errors are transient and retried with
  bounded backoff. A new module-level `replay_dead_letters(sync,
  dlq_dir)` walks the DLQ and re-drives each envelope through the
  sync, deleting on success and counting `{attempted, succeeded,
  failed, malformed}`. Four matching env vars added to `Settings`:
  `BUG_FAB_WEBHOOK_MAX_ATTEMPTS`,
  `BUG_FAB_WEBHOOK_RETRY_BACKOFF_SECONDS`, `BUG_FAB_WEBHOOK_DLQ_DIR`,
  wired through `submit.configure()` so a self-hoster who already uses
  the env-var path gets retry + DLQ by adding env vars alone. 12 new
  integration tests cover the retry classification (5xx retries, 4xx
  doesn't, timeouts retry), the negative-max-attempts clamp, the DLQ
  write/no-write paths on success/failure, and the replay loop
  (success unlinks, failure keeps the envelope, malformed JSON is
  skipped without crashing).
- Optional Slack incoming-webhook adapter at
  `bug_fab/integrations/slack.py` (new module). `SlackSync` transforms
  a `BugReportDetail`-shaped payload into a Slack Block Kit message —
  a single `attachments` entry with a severity-mapped color sidebar
  (critical / high / medium / low → red / orange / yellow / blue;
  unknown values fall back to gray) and four blocks: a header
  (`<SEVERITY>: <title>`), a section with the description (truncated
  to 500 chars to keep channel noise low), a fields section
  (Reporter, Status, optional Environment / Module), and a context
  line with the report id, timestamp, and optional `<view|...>` /
  `<github issue|...>` links. Satisfies the same `.send(report) ->
  bool` contract as `WebhookSync`, so it wires through the existing
  `webhook_sync` slot of `submit.configure()` without router changes.
  `SlackSync.from_env()` reads `BUG_FAB_SLACK_ENABLED`,
  `BUG_FAB_SLACK_WEBHOOK_URL`, `BUG_FAB_SLACK_VIEWER_BASE_URL`, and
  `BUG_FAB_SLACK_TIMEOUT_SECONDS`, returning `None` when disabled so
  it can be passed straight into `submit.configure(webhook_sync=...)`.
  Same best-effort failure-tolerance contract as the generic webhook:
  Slack outages log at WARN and never block intake. 22 new integration
  tests cover the rendered payload shape, severity-color mapping,
  description truncation, viewer-link rendering, and the standard
  failure modes (404, timeout, connect error).
- Optional marketing-site co-hosting in the `examples/error-playground/`
  POC image. A new `_resolve_marketing_dir()` helper looks for
  `/app/marketing-dist` (override via the `BUG_FAB_MARKETING_DIR` env
  var); when present, the FastAPI app mounts that directory at `/` via
  `StaticFiles(html=True)` and moves the playground to `/playground`.
  When absent, `/` falls back to serving the playground so the live demo
  never 404s. `Dockerfile` gains `COPY marketing-dist /app/marketing-dist`
  and `.gitignore` ignores the synced `marketing-dist/` directory (built
  outside the Docker context). Lets self-hosters of the POC image
  co-host a static site at root without standing up a second app.
- Playground-only abuse caps in `examples/error-playground/main.py` for
  the public POC. Two new constructs ship in the example file (not in
  the `bug_fab` package): `_CappedFileStorage` subclasses
  `bug_fab.FileStorage` and, after each successful save, deletes the
  oldest reports FIFO by `created_at` until both the report count and
  the on-disk byte total are back under cap (a cap of 0 disables that
  dimension; each drop logs `playground_evicted_report`). And
  `_BodySizeLimitMiddleware`, an ASGI middleware that rejects `POST`s
  to `/api/bug-reports` with `413` when `Content-Length` exceeds a
  budget — before uvicorn buffers the body. Three new env vars wire
  them up: `BUG_FAB_PLAYGROUND_MAX_REPORTS`,
  `BUG_FAB_PLAYGROUND_MAX_DISK_MB`, `BUG_FAB_PLAYGROUND_MAX_BODY_KB`.
  All default to `0` (off) so unit tests and local dev are unaffected;
  the public POC opts in via `fly.toml` at 500 reports / 200 MiB /
  2200 KB.
- ASP.NET Core / Razor Pages first-party adapter under
  `repo/adapters/aspnet/`. Promotes the previously private
  `notes/adapter_drafts/aspnet/` draft to a maintained reference adapter
  in the public repo. Targets `net8.0` / EF Core 8. Surface area:
  Minimal API endpoints for all eight wire-protocol routes
  (`POST /bug-reports`, `GET /reports`, `GET /reports/{id}`,
  `GET /reports/{id}/screenshot`, `PUT /reports/{id}/status`,
  `DELETE /reports/{id}`, `POST /bulk-close-fixed`,
  `POST /bulk-archive-closed`); EF Core-backed storage with
  provider-portable identity ID generation that works across SQL
  Server, PostgreSQL, SQLite, and the InMemory test provider; a
  filesystem fallback storage; `Microsoft.AspNetCore.RateLimiting`
  wired on intake with the protocol's
  `{error, detail, retry_after_seconds}` envelope on rejection;
  prefix-aware Razor views (HTML list + detail); GitHub Issues sync
  on intake. Initial EF migration committed under
  `Data/Migrations/`. Two extension methods: `AddBugFab(configuration,
  configure)` for DI registration and `UseBugFab()` for endpoint
  mounting; `MapBugFabApi()` skips the HTML viewer for headless
  deployments. xUnit suite covers intake (validation, magic bytes,
  size limit, rate limit, protocol version), viewer (list /
  detail / status update / delete), and bulk operations —
  `dotnet test` reports 18/18 passing on `dotnet 8.0.420`. Install
  path while NuGet publish is gated on a real consumer integration
  ask: pin the source via a `csproj` `<ProjectReference>` or
  `git submodule` against `repo/adapters/aspnet/src/BugFab.AspNetCore/`.
  See `repo/docs/INSTALLATION.md` § "ASP.NET Core consumer" for the
  wiring snippet and the FAB script tag.
- Annotation tools: rectangle, arrow, blur, text label. The screenshot
  canvas in the report overlay now ships a small tool palette above the
  image with the existing free-draw + eraser plus four new tools:
  click-and-drag rectangle outline, click-and-drag arrow (line + 30°
  arrowhead at the end point), click-and-drag blur (privacy-redact a
  region via `ctx.filter = "blur(12px)"` — Chromium 88+ / Firefox 103+ /
  Safari 17+), and click-to-place text label (rendered with `ctx.fillText`
  + drop shadow). Adds an undo stack (Ctrl+Z / `u`, capped at 30 strokes)
  that captures one snapshot per stroke for every tool — so undo behaves
  uniformly. Keyboard shortcuts: `d` draw, `r` rectangle, `a` arrow,
  `b` blur, `t` text, `e` eraser, `u` undo. New `annotationColor` init
  option configures the stroke color (default unchanged: `#f44336`). The
  cursor changes per tool (crosshair / IBeam / cell). No protocol change —
  the annotated PNG bytes still flow through the same `screenshot`
  multipart field. (TH-14.)
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

- Tightened the public POC deployment's package-knob defaults in
  `fly.toml` without changing any defaults inside the `bug_fab`
  package itself: `BUG_FAB_RATE_LIMIT_MAX=5` (was 50),
  `BUG_FAB_RATE_LIMIT_WINDOW_SECONDS=900` (was 3600), and
  `BUG_FAB_MAX_UPLOAD_MB=2` (was the 10-MiB default). Package defaults
  are unchanged; this only affects the live demo at the POC URL. Pairs
  with the new playground-only caps above to harden a wide-open
  internet-facing instance.
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

- Capped the auto-expanded "Auto-Captured Context" `<details>` body in
  the report overlay at `max-height: 200px` with `overflow-y: auto` (via
  the `.bug-fab-context__body` rule in `examples/error-playground/main.py`).
  When many console and network events were captured, the expanded block
  was inflating the FAB form past typical viewports and pushing the
  Submit button below the fold. The metadata plus a few events now stay
  visible while overflow scrolls inside the context box instead of the
  whole form.
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
