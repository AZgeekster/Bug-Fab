# Bug-Fab Roadmap

This is the public-facing roadmap. It mirrors the project's internal feature plan with internal-only references removed. For the contract that defines what each release ships, see the [protocol spec](./PROTOCOL.md).

If you want a feature that is not listed here, [file an issue](https://github.com/AZgeekster/Bug-Fab/issues) — particularly if you are integrating Bug-Fab into a project and have a concrete blocker. Roadmap priorities are driven by real consumer adoption, not abstract feature wishes.

---

## Versioning policy

Bug-Fab follows [semantic versioning](https://semver.org/) at the **package** level and an [independent protocol version](./PROTOCOL.md#versioning) for the wire spec. Patch releases (`0.1.x`) are bug fixes only; minor releases (`0.2`, `0.3`) add features and may add new protocol-additive fields; major releases bump the protocol version on breaking changes and ship a deprecation window.

---

## v0.1.0 — first public release (current)

The first publishable version. Establishes the wire protocol as the project's design center, ships a Python (FastAPI) reference adapter, the vanilla-JS frontend bundle, and a conformance plugin so any HTTP-speaking adapter can be verified.

### Included

- **Versioned wire protocol** — see [`PROTOCOL.md`](./PROTOCOL.md). Multipart intake (`POST /bug-reports`), JSON viewer (`GET /reports`, `GET /reports/{id}`), screenshot (`GET /reports/{id}/screenshot`), status workflow (`PUT /reports/{id}/status`), bulk operations (`POST /bulk-close-fixed`, `POST /bulk-archive-closed`), delete (`DELETE /reports/{id}`).
- **Strict severity enum** — `low | medium | high | critical`. Invalid values rejected with `422`. No silent coercion.
- **Strict status enum** — `open | investigating | fixed | closed`. Invalid values rejected with `422` on write.
- **Deprecated-values rule** — adapters MUST accept deprecated enum values on read indefinitely so long-lived stores remain readable across protocol revisions.
- **Optional `environment` field** — consumer-defined string for keeping dev / staging / prod data straight in shared collectors.
- **Server-side User-Agent capture** — request-header User-Agent is the source of truth; client-supplied `client_reported_user_agent` preserved for diagnostics.
- **Python (FastAPI) reference adapter** — `bug_fab/` package, two routers (`submit_router` + `viewer_router`) for mount-point auth delegation.
- **Three storage backends** — `FileStorage` (default, no external deps), `SQLiteStorage` (`pip install bug-fab[sqlite]`), `PostgresStorage` (`pip install bug-fab[postgres]`). Single `Storage` ABC; screenshots always on disk regardless of metadata backend.
- **Vanilla-JS frontend bundle** — floating action button (FAB), report overlay, annotation canvas (free-draw + rectangle / arrow / blur / text-label tool palette + undo + keyboard shortcuts), screenshot capture via vendored `html2canvas`, console error buffer, network log buffer, page context capture, multipart submit, CSS isolation via Shadow DOM / scoped class names. FAB position is configurable (4 corners, free-form offsets, or anchor-to-element via `stackAbove` / `stackBelow` / `stackLeft` / `stackRight`); runtime `BugFab.disable()` / `BugFab.enable()` API; per-report category dropdown; `data-submit-url` and `data-bug-fab-disabled` attributes for zero-JS configuration.
- **Vendored `html2canvas`** — pinned version, MIT license preserved. No CDN, no third-party network call. Air-gapped consumers work out of the box.
- **HTML viewer pages** — list view (filter by status / severity / environment), detail view (annotated screenshot, console / network buffers, lifecycle audit log). Severity color-coding. Configurable on/off via `viewer_enabled: bool`. Per-row "Copy Path for Claude Code" + "Reproduce" action buttons.
- **Status workflow** — inline status editor in detail view. `PUT /reports/{id}/status` endpoint with optional `fix_commit` and `fix_description`. Lifecycle audit log appends on every change.
- **Bulk operations** — `POST /bulk-close-fixed` (closes all `fixed` reports), `POST /bulk-archive-closed` (moves all `closed` reports to archive).
- **Viewer permissions config** — `viewer_permissions: {can_edit_status, can_delete, can_bulk}` gates which destructive endpoints are mounted.
- **Pagination** — `viewer_page_size: int` config option, default `20`.
- **Per-IP rate limiting** — `rate_limit_enabled: bool` (default `false`), `rate_limit_max: int` (default 50), `rate_limit_window_seconds: int` (default 3600). Returns `429` when enabled and exceeded.
- **Optional GitHub Issues sync** — opt-in. Posts a new issue per submitted bug; status changes propagate (`fixed`/`closed` → close issue; `open`/`investigating` → reopen). Best-effort: failures log but do not break submission.
- **ID format** — `bug-NNN` sequential by default. Optional `BUG_FAB_ID_PREFIX` env var enables `bug-{P|D}NNN` style for consumers who want environment-prefixed IDs.
- **Auth — mount-point delegation only.** v0.1 ships no auth abstraction; consumers protect routes by mounting Bug-Fab routers behind their existing auth middleware. The proper `AuthAdapter` ABC is v0.2.
- **Conformance test plugin** — `pytest --bug-fab-conformance --base-url=...` validates any HTTP adapter against the protocol. Adapter under test can be in any language; only the test runner is Python in v0.1.
- **Examples** — `examples/fastapi-minimal/` (10 LOC integration), `examples/flask-minimal/` (proves frontend is framework-agnostic), `examples/react-spa/` (React component wrapper around the vanilla bundle).
- **Reference adapter sketches** — non-Python sketches in [`ADAPTERS.md`](./ADAPTERS.md) for ASP.NET Core / Razor Pages, Express / Node, Fastify / TypeScript, SvelteKit, Go.
- **Adapters registry** — [`ADAPTERS_REGISTRY.md`](./ADAPTERS_REGISTRY.md) tracks every known adapter (reference, community-maintained, sketch, wanted) on a single 12-field schema, with priority tiers (T1–T4). Designed to make multi-adapter maintenance mechanical when the protocol bumps.
- **Public POC** — `examples/fastapi-minimal/` deployed on Fly.io with persistent volume. Always-on free tier. URL linked from the README.
- **CI / tooling** — GitHub Actions matrix on Python 3.10 / 3.11 / 3.12. Ruff lint + format + pytest with ≥85% coverage gate, 100% on protocol-validation. `pre-commit` framework with forbidden-strings hook.
- **Docs** — README, INSTALLATION, DEPLOYMENT_OPTIONS, PROTOCOL, ADAPTERS, CONFORMANCE, FAQ, CONTRIBUTING, ROADMAP, POC_HOSTING, CHANGELOG.

### Release cadence

1. **`v0.1.0a1`** published to PyPI immediately to reserve the package name and validate the publish workflow. Skipped by default — install via `pip install bug-fab --pre`.
2. **`v0.1.0`** final published after a real-world consumer integration validates the protocol end-to-end. `pip install bug-fab` resolves to this.

---

## v0.1.x — patch releases

**Theme:** ship bug fixes that surface from real-world adoption. No protocol changes; no scope expansion.

Patch releases will land as bugs are reported. Each:

- Tests still green; coverage gate held.
- Conformance suite still passes.
- CHANGELOG updated under `[v0.1.X] - YYYY-MM-DD`.
- PyPI publish via GitHub Actions Trusted Publishing on tag.

Likely candidates for early patches based on integration risk:

- Edge cases in multipart parsing on specific browsers.
- Storage path handling on Windows vs POSIX.
- Frontend bundle interaction with stricter Content-Security-Policy headers.
- Conformance suite false positives surfaced by adapter authors.

---

## v0.2 — auth + first non-Python adapter

**Theme:** add the proper auth abstraction (designed against real consumer integration learnings, not imagination), generalize conformance for non-Python adapters, and ship the first maintained non-Python adapter.

### Headline changes

- **`AuthAdapter` ABC** — proper auth abstraction with methods like `authenticate_viewer(request)`, `authenticate_submitter(request)`, optional `is_admin(user)`, `get_user_email(user)`. Exact shape determined by what v0.1 consumer integrations actually needed.
- **Built-in adapters:** `NoAuth()`, `CallableAuth(submit=fn, viewer=fn)`. Migration guide for v0.1 consumers documenting how to upgrade from mount-point-only auth.
- **Viewer can display submitter identity** — derived from `AuthAdapter`. Per-user filtering and audit-on-view become possible.
- **Configurable severity enum** — replace v0.1's locked `low|medium|high|critical` with consumer-defined values via config. Default still ships the v0.1 set so existing consumers do not need to change.
- **HTTP-level conformance suite** — generalize the v0.1 Python pytest plugin into curl-compatible HTTP fixtures usable by any-language adapters. Distributed as a downloadable test harness; does not require a Python install.
- **First maintained non-Python adapter** — one of: ASP.NET Core / Razor Pages, Express, SvelteKit, or Go. Selection driven by which consumer commits to integrating it first.
- **Per-user rate limiting** — replace v0.1's per-IP rate limit (which is an imperfect proxy bypassable via NAT/VPN) with per-user rate limiting keyed on `AuthAdapter.authenticate_submitter`.

### Conditional inclusions (added if a v0.1 consumer asks)

- **EF Core storage backend** (.NET) — for consumers already using EF Core.
- **Drizzle storage backend** (TypeScript) — for SvelteKit / Node consumers using Drizzle.
- **MySQL via SQLAlchemy** — yet to surface as a real ask, but cheap to add if it does.
- **Opt-out html2canvas build** — smaller bundle for SPA consumers who supply their own screenshot library.

### Release criteria

- Wire protocol either unchanged from v0.1 OR breaking changes published with a v0.1 → v0.2 deprecation plan.
- All v0.1 consumers can opt into v0.2 without code changes (additive only) — or migration guide documents the required changes.
- At least one non-Python adapter shipped and has a real consumer integration validating it.
- HTTP-level conformance fixtures published and passing.

---

## v0.2+ candidates

These are speculative — they will land when there is concrete demand from a real consumer integration. Listed here so contributors know they are on the maintainer's radar.

| Feature | Trigger |
|---------|---------|
| Comment threads on bug reports | First consumer asks |
| Assignment (assignee field per report) | First consumer asks |
| ~~Webhook outbound (Slack / Discord / Teams / arbitrary URL)~~ | **SHIPPED in v0.1** — `bug_fab.integrations.webhook.WebhookSync` (FastAPI/Flask) + `bug_fab.adapters.django.webhook_sync.send` (Django). See [`DEPLOYMENT_OPTIONS.md` § Webhook delivery](DEPLOYMENT_OPTIONS.md#webhook-delivery). |
| Email notifications on new submission / status change | First consumer asks |
| i18n of the frontend overlay strings | First non-English consumer |
| Theming (CSS variables for FAB / overlay colors) | First consumer with strict brand requirements |
| Mobile-friendly overlay layout | After v0.1 polish pass; mobile is real work, not a default |
| Server-side screenshot redaction (auto-blur passwords) | After threat-model review confirms the need; this is a hard problem with subtle privacy implications |
| Opt-out html2canvas build | First SPA consumer requests it |
| `npm` distribution of the standalone JS bundle | First SPA consumer prefers `npm install` to `<script>` tag |
| Additional first-class non-Python adapters | Each driven by a real consumer commit |

---

## Out of scope — forever

These features are explicitly **not** part of Bug-Fab's roadmap. They are different tools serving different needs; if you want one, use the right tool for that job. Bug-Fab integrates with several of them via GitHub sync and (eventually) webhooks.

| Out of scope | Right tool for the job |
|--------------|------------------------|
| Telemetry / product analytics | Mixpanel, PostHog, Plausible |
| Automatic error monitoring | Sentry, Rollbar — Bug-Fab is user-initiated; error monitoring is automatic. Different design. |
| Logging infrastructure | Loki, Datadog — the console buffer is just enough context for one user-submitted report, not a logging pipeline |
| Issue tracking workflow | Jira, Linear, GitHub Issues — Bug-Fab bridges to existing trackers via GitHub sync; it does not manage workflow |
| Customer support chat / ticketing | Intercom, Zendesk — different audience, different surface |
| A/B testing / feature flags | Unleash, Statsig, LaunchDarkly — unrelated |
| Hosted SaaS | Bug-Fab is self-hosted by design. There is no managed offering and no plan to operate one. |
| Session replay / video capture as a primary feature | FullStory, LogRocket — Bug-Fab might add a short capture as supporting context in v1.0+, but the product is not a replay tool |

The principle: Bug-Fab is **for bug reporting from a running web app**. If a feature request would expand beyond that, it gets resisted as scope creep.

---

## How to influence the roadmap

1. **Integrate Bug-Fab into a real project.** This is by far the strongest signal. Real adoption surfaces real gaps.
2. **File an issue** describing your blocker, your stack, and what you tried. Tag with `roadmap-input`.
3. **Open a PR** for documentation gaps you hit during integration. Even small clarifications to [`PROTOCOL.md`](./PROTOCOL.md) or [`ADAPTERS.md`](./ADAPTERS.md) help.
4. **Build an adapter.** If you ship a maintained adapter for a non-Python stack and it passes [conformance](./CONFORMANCE.md), it can be linked from this roadmap and from [`ADAPTERS.md`](./ADAPTERS.md).

The maintainer is one person doing this in their spare time. Roadmap priorities will track real adoption; speculative feature requests without an integration backing them are unlikely to move ahead of v0.2 priorities.
