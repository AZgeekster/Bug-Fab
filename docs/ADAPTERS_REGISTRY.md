# Bug-Fab Adapters Registry

This document is the canonical list of Bug-Fab wire-protocol adapters across stacks and languages. It serves three purposes:

1. **Discovery** — show consumers what's available for their stack.
2. **Triage** — rank candidate adapter targets by priority so contributors know where help is most useful.
3. **Maintenance** — give every adapter the same metadata schema so a maintainer can scan-and-update many entries quickly when the protocol bumps.

**Bug-Fab's stance on adapters:** the wire protocol is the contract. Per the [decisions log](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md), the project does NOT speculatively ship maintained adapter packages. The reference Python (FastAPI) adapter is the only first-party package. Other adapters are either consumer-maintained or documented as code-level sketches in [`ADAPTERS.md`](./ADAPTERS.md). When ≥3 consumers ask for the same adapter, it becomes a candidate for first-party maintenance.

---

## Status legend

| Status | Meaning |
|--------|---------|
| 🟢 **reference** | Bug-Fab's official first-party adapter. Source-of-truth implementation. |
| 🔵 **community-maintained** | A consumer-built adapter, listed here as a known reference. Not maintained by Bug-Fab. |
| 🟡 **sketch** | Code-level walkthrough lives in [`ADAPTERS.md`](./ADAPTERS.md). No published package. |
| ⚪ **wanted** | High-priority target with no implementation yet. Contributions welcome. |
| ⚫ **out-of-scope (v0.1)** | Acknowledged but not on the v0.1 roadmap. Likely v0.2+ or never. |

## Entry schema

Every adapter entry below uses the same fixed schema so multi-plugin maintenance stays mechanical. When the wire protocol bumps, scan every entry's `Tracks Bug-Fab` row, mark `outdated`, ping maintainers, repeat. No bespoke per-adapter checklists.

```
| Field            | Value                                                          |
|------------------|----------------------------------------------------------------|
| Stack            | Framework version range + language version requirement         |
| Status           | 🟢 / 🔵 / 🟡 / ⚪ / ⚫                                            |
| Tier             | 1 (highest priority) ... 4 (specialty)                         |
| Package          | Published name + registry, or "(unpublished)" or "(sketch)"    |
| Repository       | Link, or "(this repo)" for first-party                         |
| Language         | TypeScript / Python / Go / etc.                                |
| Tracks Bug-Fab   | Protocol version the adapter implements (e.g., "v0.1")         |
| Conformance      | "✅ passing 2026-04-30" / "⚠️ partial" / "❌ failing" / "untested" |
| Reference doc    | `docs/ADAPTERS.md#…` or external link                          |
| Last updated     | YYYY-MM-DD                                                     |
| Maintainer       | "Bug-Fab core" / GitHub handle / "(none — sketch only)"        |
| Notes            | One sentence. Migration friction, conformance gaps, etc.       |
```

The 12 fields are intentionally minimal — anything more should live in the adapter's own README, not here.

---

## Tier 1 — high priority

Mainstream stacks where adapter demand is likely from real consumers. Bug-Fab actively shepherds these.

### FastAPI (Python)

| Field | Value |
|---|---|
| Stack | FastAPI ≥ 0.110, Python ≥ 3.10 |
| Status | 🟢 reference |
| Tier | 1 |
| Package | `bug-fab` on PyPI |
| Repository | (this repo — `bug_fab/`) |
| Language | Python |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ — the reference adapter defines the conformance suite's expectations |
| Reference doc | `bug_fab/routers/` source + `docs/INSTALLATION.md` |
| Last updated | 2026-04-30 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | Source-of-truth implementation. File / SQLite / Postgres backends; optional GitHub Issues sync. |

### Fastify (TypeScript / Node ≥ 20)

| Field | Value |
|---|---|
| Stack | Fastify ≥ 5, `@fastify/multipart` ≥ 10 |
| Status | 🔵 community-maintained |
| Tier | 1 |
| Package | `fastify-bug-fab` (TKR-maintained, unpublished as of 2026-04-30) |
| Repository | TKR private workspace; corrected snapshot at `notes/tkr_corrected_plugin_2026-04-29/` |
| Language | TypeScript |
| Tracks Bug-Fab | v0.1 (post-spec-tightening 2026-04-29) |
| Conformance | ⚠️ partial — pending first run on corrected plugin |
| Reference doc | Sketch: [`docs/ADAPTERS.md#fastify-typescript-fastify--5`](./ADAPTERS.md#fastify-typescript-fastify--5). Full walkthrough: [`docs/integrations/fastify-nextjs-postgres.md`](./integrations/fastify-nextjs-postgres.md) (+ [AI companion](./integrations/fastify-nextjs-postgres.AGENTS.md)). |
| Last updated | 2026-04-30 |
| Maintainer | TKR (Andrew's other project) |
| Notes | First-ever consumer integration; surfaced the spec-tightening pass. Now spec-conformant after 7 mechanical fixes. |

### Flask (Python)

| Field | Value |
|---|---|
| Stack | Flask ≥ 3, Python ≥ 3.10 |
| Status | 🟢 reference (first-party shim) |
| Tier | 1 |
| Package | `bug-fab[flask]` extra (ships in main wheel; Flask installed only when extra is selected) |
| Repository | `bug_fab/adapters/flask/` (shim) + `examples/flask-minimal/` (reference consumer) |
| Language | Python |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ passing 2026-05-01 — `pytest --bug-fab-conformance --base-url=http://127.0.0.1:8000/bug-fab` returns 29/29 against `examples/flask-minimal/main.py` |
| Reference doc | `bug_fab/adapters/flask/` source + `examples/flask-minimal/README.md` |
| Last updated | 2026-05-01 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | First-party `make_blueprint(settings)` factory. Consumer integration drops to ~10 LOC. Reuses `bug_fab.intake.validate_payload` so protocol drift is impossible by construction. GitHub Issues sync wired on intake + status update (mirrors FastAPI router). Async bridge via `asyncio.run` per request — see module docstring for the trade-off. Mount-prefix MUST be non-empty; the viewer's HTML list page lives at the blueprint's root path. |

### Express (TypeScript / Node)

| Field | Value |
|---|---|
| Stack | Express ≥ 4, Node ≥ 20 |
| Status | 🟡 sketch |
| Tier | 1 |
| Package | (sketch) |
| Repository | — |
| Language | TypeScript |
| Tracks Bug-Fab | v0.1 |
| Conformance | untested |
| Reference doc | [`docs/ADAPTERS.md#express--nodejs`](./ADAPTERS.md#express--nodejs) |
| Last updated | 2026-04-29 (TKR-feedback round) |
| Maintainer | (none — sketch only) |
| Notes | The Fastify section of ADAPTERS.md is more current; Express still uses `multer` + callback patterns that may need a refresh once a real Express consumer surfaces. |

### Next.js Route Handlers (TypeScript)

| Field | Value |
|---|---|
| Stack | Next.js ≥ 14 (App Router), TypeScript |
| Status | 🟡 sketch |
| Tier | 1 |
| Package | (sketch) |
| Repository | — |
| Language | TypeScript |
| Tracks Bug-Fab | v0.1 |
| Conformance | untested |
| Reference doc | [`docs/ADAPTERS.md#nextjs-route-handlers-typescript-nextjs--14-app-router`](./ADAPTERS.md#nextjs-route-handlers-typescript-nextjs--14-app-router) |
| Last updated | 2026-04-30 |
| Maintainer | (none — sketch only) |
| Notes | Co-locates Bug-Fab with the Next.js app — no separate Fastify process. Real fit for Vercel / Cloudflare Pages deployments where serverless functions are the backend. Sketch covers all 8 endpoints; a full `examples/nextjs-minimal/` example app is still a v0.1.x backlog item. |

### Django (Python)

| Field | Value |
|---|---|
| Stack | Django ≥ 4.2, Python ≥ 3.10 |
| Status | 🟢 reference (first-party reusable app) |
| Tier | 1 |
| Package | `bug-fab[django]` extra (ships in main wheel; Django installed only when extra is selected) |
| Repository | `bug_fab/adapters/django/` (reusable app) + `examples/django-minimal/` (reference consumer) |
| Language | Python |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ passing 2026-05-01 — `pytest --bug-fab-conformance --base-url=http://127.0.0.1:8765/api --viewer-base-url=http://127.0.0.1:8765/admin/bug-reports` returns 29/29 against a live `runserver` |
| Reference doc | `bug_fab/adapters/django/` source + `examples/django-minimal/README.md` |
| Last updated | 2026-05-01 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | First-party reusable Django app: register in `INSTALLED_APPS`, run `migrate`, mount the intake + viewer URLconfs. Native Django ORM models, `BugReportAdmin` for admin UI, plain Django views (no DRF dependency). Validation reuses `bug_fab.intake.validate_payload` so the wire-protocol contract is shared with the FastAPI reference. Sync-by-design (`DjangoORMStorage`); not a thin shim over `bug_fab.storage.Storage` ABC because Django's ORM is sync. |

### NestJS (TypeScript)

| Field | Value |
|---|---|
| Stack | NestJS ≥ 10, TypeScript |
| Status | 🟡 sketch |
| Tier | 1 |
| Package | (sketch) |
| Repository | — |
| Language | TypeScript |
| Tracks Bug-Fab | v0.1 |
| Conformance | untested |
| Reference doc | [`docs/ADAPTERS.md#nestjs-typescript-nestjs--10`](./ADAPTERS.md#nestjs-typescript-nestjs--10) |
| Last updated | 2026-04-30 |
| Maintainer | (none — sketch only) |
| Notes | `BugFabModule` pattern: 2 controllers (intake + viewer), `BugFabService` implementing IStorage, class-validator DTOs, custom `@Catch()` exception filter remapping NestJS's default `{statusCode, message, error}` to the protocol's `{error, detail}` envelope. Targets `@nestjs/platform-fastify` (recommended) with Express path noted. 9-bullet pitfalls section. |

---

## Tier 2 — medium priority

Real but smaller user bases or growing-mainstream stacks. Sketches welcome; maintained packages on demand.

### Hono (TypeScript)

| Field | Value |
|---|---|
| Stack | Hono ≥ 4 (Node, Bun, Deno, Cloudflare Workers, Vercel Edge) |
| Status | 🟡 sketch |
| Tier | 2 |
| Package | (sketch) |
| Repository | — |
| Language | TypeScript |
| Tracks Bug-Fab | v0.1 |
| Conformance | untested |
| Reference doc | [`docs/ADAPTERS.md#hono-typescript-hono--4`](./ADAPTERS.md#hono-typescript-hono--4) |
| Last updated | 2026-04-30 |
| Maintainer | (none — sketch only) |
| Notes | Edge-runtime-friendly. Sketch uses `c.req.parseBody()` for multipart, returns `Uint8Array` from `getScreenshotBytes` (so storage backends can be R2/S3/KV instead of `node:fs` for serverless deploys). Body-size limit is runtime-defined; 10/11 MiB cap enforced inside the handler. |

### SvelteKit (TypeScript)

| Field | Value |
|---|---|
| Stack | SvelteKit ≥ 2 |
| Status | 🟡 sketch |
| Tier | 2 |
| Package | (sketch) |
| Repository | — |
| Language | TypeScript |
| Tracks Bug-Fab | v0.1 |
| Conformance | untested |
| Reference doc | [`docs/ADAPTERS.md#sveltekit`](./ADAPTERS.md#sveltekit) |
| Last updated | 2026-04-29 (TKR-feedback round) |
| Maintainer | (none) |
| Notes | `+server.ts` files map naturally to the protocol. Drizzle ORM example included. |

### ASP.NET Core / Razor Pages (.NET)

| Field | Value |
|---|---|
| Stack | ASP.NET Core ≥ 8, EF Core 8 |
| Status | 🟢 reference (first-party adapter) |
| Tier | 2 |
| Package | (unpublished — in-repo source under `repo/adapters/aspnet/`; NuGet publish gated on first real consumer integration ask) |
| Repository | `repo/adapters/aspnet/` (this repo) |
| Language | C# |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ in-repo xUnit suite green — `dotnet test` returns 18/18 passing (verified 2026-05-04); cross-stack `pytest --bug-fab-conformance --base-url=http://127.0.0.1:5000/bug-fab` against a live `dotnet run` server still pending (requires booting both processes; tracked as next milestone) |
| Reference doc | [`docs/ADAPTERS.md#aspnet-core--razor-pages-net-8`](./ADAPTERS.md#aspnet-core--razor-pages-net-8) + `repo/adapters/aspnet/README.md` |
| Last updated | 2026-05-04 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | Minimal API endpoints (not `[ApiController]`), provider-portable identity ID generation (`ValueGeneratedOnAdd` + `MAX(IdSequence)+1` fallback for InMemory), `Microsoft.AspNetCore.RateLimiting` wired for intake with the protocol's `{error, detail, retry_after_seconds}` envelope on rejection, prefix-aware Razor views via named routes, `EnableAntiforgeryOnViewer` flag declared (v0.2 wiring). 1500+ LOC C# across the engine + tests. Initial EF migration committed under `Data/Migrations/`. Next milestone: live cross-stack `pytest --bug-fab-conformance` run + NuGet package publish. |

### Ruby on Rails

| Field | Value |
|---|---|
| Stack | Rails ≥ 7.1, Ruby ≥ 3.2 |
| Status | 🟡 draft (test-suite-green, not yet promoted) |
| Tier | 2 |
| Package | (private draft) |
| Repository | private `notes/adapter_drafts/rails/` (not yet promoted to a public repo / RubyGems) |
| Language | Ruby |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ 22/22 in-repo tests pass on Ruby 3.3 + Rails 7.1.6 (verified 2026-05-04 via `docker run --rm ruby:3.3 bundle exec rake test`); `pytest --bug-fab-conformance` against a live `bin/rails server` not yet run |
| Reference doc | [`docs/ADAPTERS.md` § Rails](./ADAPTERS.md) — and the in-draft `README.md` |
| Last updated | 2026-05-04 |
| Maintainer | (none — private draft) |
| Notes | Mountable Rails Engine. ActiveRecord models mirror the Python `BugReportORM` schema (with `module → module_name` rename to avoid Ruby's `Module` constant clash). Test harness uses the canonical `rails plugin new --mountable --dummy-path=test/dummy` scaffold (an earlier inline `TestApplication` pattern double-evaluated engine routes; replaced 2026-05-04). Promotion path: move to `repo/adapters/rails/` or sibling `bug_fab-rails` repo, run upstream conformance, publish to RubyGems. |

### Go + Gin

| Field | Value |
|---|---|
| Stack | Gin v1.10+, Go 1.22+ |
| Status | 🟢 reference (first-party adapter) |
| Tier | 2 |
| Package | (unpublished — `go get github.com/AZgeekster/Bug-Fab/adapters/go-gin/bugfab` direct from the in-repo module) |
| Repository | `repo/adapters/go-gin/` (this repo) |
| Language | Go |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ in-repo `go test ./...` returns 37/37 passing at 75.7% statement coverage (verified 2026-05-21 via `docker run --rm golang:1.22`); cross-stack `pytest --bug-fab-conformance` against a live `go run` server pending a Go runner (v0.2 candidate) |
| Reference doc | [`docs/ADAPTERS.md`](./ADAPTERS.md) — and `repo/adapters/go-gin/README.md` |
| Last updated | 2026-05-21 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | File storage layout is byte-for-byte cross-readable with the Python reference (a `bug-NNN.json` written by either side parses on the other). In-process token-bucket rate limiter; mount-point auth delegation (no built-in auth). Custom `MarshalJSON`/`UnmarshalJSON` on `BugReportContext` preserves the Pydantic `extra="allow"` round-trip contract. |

### Rust + Axum

| Field | Value |
|---|---|
| Stack | Axum 0.7+, Rust 1.75+ for default file storage / Rust 1.86+ when the optional `sqlx` SQLite backend is enabled |
| Status | 🟢 reference (first-party adapter) |
| Tier | 2 |
| Package | (unpublished — path/git dep on the in-repo workspace until the first crates.io tag) |
| Repository | `repo/adapters/rust-axum/` (this repo) |
| Language | Rust |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ `cargo test --workspace` returns 21/21 passing (7 unit + 8 integration + 5 storage roundtrip + 1 doctest) under `docker run --rm rust:1.75` (verified 2026-05-21); cross-stack `pytest --bug-fab-conformance` against a live `cargo run -p bugfab-example` server pending |
| Reference doc | `repo/adapters/rust-axum/README.md` + `MIGRATION_NOTES.md` |
| Last updated | 2026-05-21 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | Split MSRV: 1.75 for the file-only default; enabling `--features sqlx` jumps the floor to 1.86 because sqlx's transitive `idna_adapter` / `icu_*` deps require edition 2024. `Arc<dyn Storage + Send + Sync>` injection pattern documented in MIGRATION_NOTES — keeps the route handlers generic-free at the cost of one `async_trait` macro until native async-fn-in-trait dropdrops the MSRV. |

### Laravel (PHP)

| Field | Value |
|---|---|
| Stack | Laravel 11.x, PHP 8.3+ |
| Status | 🟢 reference (first-party adapter) |
| Tier | 2 |
| Package | `bugfab/laravel-adapter` (in-repo source; not yet on Packagist — install via Composer path / VCS until the first tag) |
| Repository | `repo/adapters/laravel/` (this repo) |
| Language | PHP |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ PHPUnit 11.5.55 runs 33/33 tests (89 assertions) under `docker run --rm composer:2` (verified 2026-05-21); cross-stack `pytest --bug-fab-conformance` against a live `php artisan serve` instance pending |
| Reference doc | `repo/adapters/laravel/README.md` + `MIGRATION_NOTES.md` |
| Last updated | 2026-05-21 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | Two storage backends (FileStorage + EloquentStorage), Service Provider auto-discovers via `extra.laravel.providers`. Octane-aware but **watch the default `array` cache driver** — per-worker rate-limit counters multiply the effective limit; switch to Redis for accurate per-IP limiting (documented in MIGRATION_NOTES). Native PHP `enum` + `Rule::enum()` for severity validation; intake route is CSRF-exempt because the bundle posts cross-page. |

### Elixir + Phoenix

| Field | Value |
|---|---|
| Stack | Phoenix ≥ 1.7, Elixir ≥ 1.16, OTP ≥ 26 |
| Status | 🟢 reference (first-party adapter) |
| Tier | 2 |
| Package | (unpublished — Hex.pm publish pending tag) |
| Repository | `repo/adapters/phoenix/` (this repo) |
| Language | Elixir |
| Tracks Bug-Fab | v0.1 |
| Conformance | ✅ `mix test` returns 37/37 passing under `docker run --rm elixir:1.16` (verified 2026-05-21); cross-stack `pytest --bug-fab-conformance` pending |
| Reference doc | `repo/adapters/phoenix/README.md` + `MIGRATION_NOTES.md` |
| Last updated | 2026-05-21 |
| Maintainer | Bug-Fab core (AZgeekster) |
| Notes | Mountable `Plug.Router` under any Phoenix endpoint OR as a standalone `Plug.Cowboy` app — keeps non-Phoenix Plug consumers happy. Two storage backends (FileStorage + EctoStorage). Minimal embedded EEx viewer; LiveView upgrade earmarked for v0.2. Telemetry events listed in MIGRATION_NOTES but not yet wired. |

---

## Tier 3 — lower priority

Smaller adoption or specialized stacks. Sketches OK; nothing actively pursued.

### Go (`net/http` + `chi`)

| Field | Value |
|---|---|
| Stack | Go ≥ 1.22, `chi/v5` |
| Status | 🟡 sketch |
| Tier | 3 |
| Package | (sketch) |
| Repository | — |
| Language | Go |
| Tracks Bug-Fab | v0.1 |
| Conformance | untested |
| Reference doc | [`docs/ADAPTERS.md#go-nethttp--chi`](./ADAPTERS.md#go-nethttp--chi) |
| Last updated | 2026-04-29 |
| Maintainer | (none) |
| Notes | Go ecosystem is small relative to Python/TS. PostgreSQL persistence sketch included. |

### Laravel (PHP)

| Field | Value |
|---|---|
| Stack | Laravel ≥ 11, PHP ≥ 8.2 |
| Status | ⚫ out-of-scope (v0.1) |
| Tier | 3 |
| Package | (none) |
| Repository | — |
| Language | PHP |
| Tracks Bug-Fab | (none) |
| Conformance | n/a |
| Reference doc | — |
| Last updated | 2026-04-30 |
| Maintainer | (none) |
| Notes | PHP is not in Bug-Fab's current consumer mix. Re-evaluate if a Laravel consumer surfaces. |

### Phoenix (Elixir)

| Field | Value |
|---|---|
| Stack | Phoenix ≥ 1.7 |
| Status | ⚫ out-of-scope (v0.1) |
| Tier | 3 |
| Package | (none) |
| Repository | — |
| Language | Elixir |
| Tracks Bug-Fab | (none) |
| Conformance | n/a |
| Reference doc | — |
| Last updated | 2026-04-30 |
| Maintainer | (none) |
| Notes | Devoted but small ecosystem. A `bug_fab` hex package would be welcome but not on the roadmap. |

---

## Tier 4 — specialty / experimental

Different deployment models or unusual frontends. Not a priority but acknowledged.

### Cloudflare Workers (TypeScript / edge runtime)

| Field | Value |
|---|---|
| Stack | Cloudflare Workers, TypeScript |
| Status | ⚫ out-of-scope (v0.1) |
| Tier | 4 |
| Notes | Storage backend would need to use Workers KV or R2 (no filesystem). A Hono-based adapter would fit naturally once Hono is done. |

### AWS Lambda / serverless

| Field | Value |
|---|---|
| Stack | Any Lambda runtime (Node, Python, Go) |
| Status | ⚫ out-of-scope (v0.1) |
| Tier | 4 |
| Notes | The remote-collector pattern (per `docs/DEPLOYMENT_OPTIONS.md`) already covers this — point Lambdas at a hosted Bug-Fab collector instead of running Bug-Fab inside Lambda. |

### WordPress plugin (PHP)

| Field | Value |
|---|---|
| Stack | WordPress ≥ 6.5 |
| Status | ⚫ out-of-scope (v0.1) |
| Tier | 4 |
| Notes | Large user base, very different model from web apps. Frontend bundle would work; backend would need a custom REST API endpoint. Realistic only if a WordPress consumer surfaces. |

---

## Adapter authorship checklist

When you build a new adapter (whether sketch, community-maintained, or first-party), use this checklist to ensure consistency. Each item links to the canonical Bug-Fab definition; do NOT improvise on protocol shape.

1. **Wire-protocol contract** — implement the JSON Schema at [`docs/protocol-schema.json`](./protocol-schema.json). Prose is at [`PROTOCOL.md`](./PROTOCOL.md). Schema wins on disagreement.
2. **Eight endpoints** — `POST /bug-reports`, `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/screenshot`, `PUT /reports/{id}/status`, `DELETE /reports/{id}`, `POST /bulk-close-fixed`, `POST /bulk-archive-closed`. All return the documented response shapes.
3. **`IStorage` interface** — 8 wire-required methods (`saveReport`, `getReport`, `listReports`, `getScreenshotPath`, `updateStatus`, `deleteReport`, `bulkCloseFixed`, `bulkArchiveClosed`) plus the optional `setGitHubIssue` post-save hook. Adapters MAY add a private single-report archive helper (the Python `Storage` ABC has `archiveReport` for this) since `bulkArchiveClosed` typically needs to move one report at a time, but the wire protocol exposes only the bulk variant — single-report archive is implementation-detail, not part of the cross-adapter contract.
4. **Validation** — magic-byte PNG check, severity / status / report_type strict rejection (no silent coercion), `protocol_version === "0.1"` required, `client_ts` required non-empty, `reporter` sub-fields capped at 256 chars.
5. **Error envelope** — `{ "error": "<code>", "detail": "<string-or-array>" }` for every non-2xx except `204`. Codes per [`PROTOCOL.md` § Error responses](./PROTOCOL.md).
6. **Mount-prefix invariant** — viewer MUST be mounted under a non-empty prefix (it serves an HTML list at the prefix root).
7. **Auth** — adapter exposes routes; consumer protects them at the mount point. v0.1 has no auth abstraction.
8. **GitHub sync** — best-effort. Failures log; do NOT fail the intake response.
9. **Lifecycle log** — append-only. `created` / `status_changed` / `deleted` / `archived`. Use `"anonymous"` or `null` when the adapter has no auth context.
10. **Conformance** — pass `pytest --bug-fab-conformance --base-url=<your-adapter>`. The Python `bug-fab` package ships the conformance plugin; install with `pip install --pre bug-fab`.
11. **Snake_case** — JSON keys are snake_case across the wire. No camelCase plugins / converters on Bug-Fab routes.
12. **Documentation** — your adapter's repo MUST include a README, an AGENTS.md (for AI assistants integrating against it), and a license. Reference the upstream Bug-Fab spec.

---

## Adding to this registry

Send a PR against `repo/docs/ADAPTERS_REGISTRY.md` with a new entry following the schema at the top of this file. Include:

- A status badge (🔵 if you'll maintain it; ⚪ if you're requesting it but not building it).
- An entry table with all 12 fields populated.
- An entry under the appropriate Tier section (re-evaluate priority if you have data).

Bug-Fab maintainers will review and merge. Tier reassignment requires a one-paragraph case in the PR description.

---

## Maintenance procedure (when the wire protocol bumps)

When a new Bug-Fab protocol version ships (v0.2 etc.):

1. Increment the `Tracks Bug-Fab` row of the reference adapter (FastAPI) to the new version.
2. For every other entry, change `Tracks Bug-Fab` to `v0.1 (outdated as of <date>)` and `Conformance` to `❌ outdated until verified`.
3. Open a tracking issue per Tier-1 / Tier-2 entry pinging the maintainer (if any) with a link to the protocol changelog.
4. Re-run the conformance suite against any adapter where the maintainer reports an upgrade.
5. Update the entry's `Last updated` row.

This procedure is the reason every entry has the same 12-field schema — it's a sed-/awk-friendly format that scales to many adapters without manual per-entry decisions.
