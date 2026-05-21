# AGENTS.md — bug_fab-rails

Operating manual for AI assistants working in this gem.

## What this is

A Rails Engine adapter for [Bug-Fab](https://github.com/AZgeekster/Bug-Fab), a framework-agnostic bug-reporting tool. The wire protocol is the contract; this gem implements the Ruby/Rails side. Authoritative spec: `repo/docs/PROTOCOL.md` and `repo/docs/protocol-schema.json` in the Bug-Fab repo.

## Authoritative references (in priority order)

1. **`docs/protocol-schema.json`** in the Bug-Fab repo — the JSON Schema is the contract. If it disagrees with prose, the schema wins.
2. **`docs/PROTOCOL.md`** — prose commentary on the schema. Read for rationale.
3. **`docs/ADAPTERS_REGISTRY.md` § "Adapter authorship checklist"** — the 12-point conformance gate every adapter must satisfy.
4. **`bug_fab/routers/submit.py` and `viewer.py`** — Python reference. When a behavior question arises, the reference implementation is the tiebreaker.
5. **`bug_fab/storage/_models.py`** — the schema this engine's ActiveRecord models mirror.

## Don't break these invariants

- **Snake_case JSON keys.** Rails' default `to_json` does not transform key case — match the schema's snake_case verbatim. Do NOT install `key_transform` middleware on Bug-Fab routes.
- **PNG-only screenshots.** Magic-byte check (`"\x89PNG\r\n\x1a\n"`) on intake. Reject anything else with `415 unsupported_media_type`.
- **Strict enum write-side rejection.** Severity / status / report_type / protocol_version mismatch → 422 (or 400 for protocol_version) with the documented error code. Silent coercion fails conformance.
- **Permissive enum read-side acceptance.** Existing rows with deprecated values (e.g., a legacy `"resolved"` status) MUST still render via list and detail endpoints. The `BugReport` model never re-validates the column on read.
- **Screenshots on disk, never in the DB.** Active Storage is forbidden. `screenshot_path` is the only DB pointer. Files live under `BugFab.configuration.resolved_storage_root`.
- **Mount-prefix invariant.** The viewer index serves at the mount root (an empty route relative to the engine). The engine MUST be mounted under a non-empty prefix.
- **GitHub sync is sync best-effort.** No Sidekiq / ActiveJob. Failures log via `Rails.logger.warn` and return nil. A GitHub outage MUST NOT fail an otherwise-valid bug submission.
- **Lifecycle log is append-only.** `created` on intake, `status_changed` on every successful PUT, `archived` on bulk archive. Field names lock to `action / by / at`.
- **Dual user-agent capture.** Server reads `request.user_agent` and stores it as `server_user_agent`; the client value (from `context.user_agent`) is preserved separately as `client_reported_user_agent`. Never overwrite one with the other.

## Directory map

```
lib/
├── bug_fab/
│   ├── rails.rb            main require + BugFab.configure entry point
│   ├── engine.rb           Rails::Engine subclass, isolate_namespace
│   ├── version.rb          gem + protocol version constants
│   ├── configuration.rb    Configuration POODR object
│   ├── validation.rb       protocol checks (severity, status, etc.)
│   ├── errors.rb           protocol error envelope helpers
│   └── github.rb           best-effort GitHub Issues sync
├── generators/bug_fab/install/
│   ├── install_generator.rb     `bin/rails g bug_fab:install`
│   └── templates/initializer.rb.tt
app/
├── controllers/bug_fab/
│   ├── application_controller.rb   rescue_from + permission gating
│   ├── reports_controller.rb       6 of 8 endpoints
│   ├── bulk_actions_controller.rb  bulk-close-fixed + bulk-archive-closed
│   └── screenshots_controller.rb   raw PNG bytes
├── models/bug_fab/
│   ├── application_record.rb       abstract class
│   ├── bug_report.rb               main model + class-level operations
│   └── bug_report_lifecycle.rb     append-only audit row
├── views/bug_fab/                  HTML viewer (ported from upstream Jinja templates)
└── assets/javascripts/bug_fab/     vendor the upstream bug-fab.js bundle here at build time
db/migrate/                         single initial migration
test/                               minitest; integration conformance test
```

## Common tasks

### Add a new endpoint

If the protocol bumps and adds an endpoint:

1. Update the route in `config/routes.rb` (use the same constraint regex `/bug-[A-Za-z]?\d{1,12}/` for ID params).
2. Add a controller action in `ReportsController` (or a new namespaced controller if it's a distinct concern).
3. Add a migration if storage shape changes.
4. Add a test in `test/integration/conformance_test.rb` that exercises the new endpoint inline.
5. Update README's endpoint table.

### Validate an unknown field shape

Edit `lib/bug_fab/validation.rb`. Use the existing `Errors.schema_error!` /  `Errors.validation_error!` / `Errors.unsupported_protocol_version!` helpers — do not invent new error codes without updating PROTOCOL.md. The five codes documented there are: `validation_error`, `unsupported_protocol_version`, `payload_too_large`, `unsupported_media_type`, `schema_error`.

### Bump the protocol version

1. Update `BugFab::PROTOCOL_VERSION` in `lib/bug_fab/version.rb`.
2. Update `Validation.validate_create!` to accept the new value.
3. Per the Bug-Fab "deprecated values rule," the engine MUST continue to read existing rows submitted under the prior version. Do NOT block reads.

## Testing

- `bundle exec rake test` runs the full minitest suite.
- The integration conformance test in `test/integration/conformance_test.rb` boots the engine inline (no `test/dummy/` app needed) and hits every endpoint.
- Authoritative external conformance is the Python pytest plugin (`pytest --bug-fab-conformance`).

## What NOT to do

- Do NOT reach for `dry-validation`, `dry-schema`, or `JSON-Schema` to do validation. The current hand-rolled validators are intentional — runtime dependency surface stays at "Rails" only.
- Do NOT introduce Active Storage, CarrierWave, or Shrine. Screenshots are flat files.
- Do NOT introduce Sidekiq, GoodJob, or ActiveJob. GitHub sync is sync best-effort.
- Do NOT enable Rails' default CSRF on intake. The JS bundle posts from a context where CSRF tokens aren't available; `protect_from_forgery with: :null_session` is the correct posture.
- Do NOT camelCase JSON keys (no `key_transform`, no JSONAPI gems).
- Do NOT mention private consumer projects, employer names, or internal infrastructure in any file. This gem is open-source.
