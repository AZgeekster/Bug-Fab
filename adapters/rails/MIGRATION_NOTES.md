# Migration Notes — Rails / Ruby specifics

Things future consumers should know that don't fit in the README.

## Mounting the Engine

`BugFab::Engine` is a `Rails::Engine` with `isolate_namespace BugFab`. Mount it at a non-empty prefix:

```ruby
# config/routes.rb
mount BugFab::Engine, at: "/bug-fab"
```

The viewer HTML index serves at the **engine mount root** (an empty route relative to the engine), so mounting at `/` would collide with the host application's root route. The protocol's mount-prefix invariant requires a non-empty mount; the adapter does NOT enforce this at boot — a misconfigured `at: "/"` will simply 404 the viewer and silently shadow the host root if you mount via wildcard. Pick a real prefix.

The engine's `db/migrate/` path is auto-appended to the host's migration paths via the `bug_fab.append_migrations` initializer. Consumers run `bin/rails db:migrate` once after `bundle install` and the `bug_fab_bug_reports`, `bug_fab_lifecycle_events`, and `bug_fab_id_counter` tables appear. No separate `bug_fab:install:migrations` step is needed.

## ActiveRecord vs FileStorage tradeoffs

This adapter ships **only** the ActiveRecord storage backend. Unlike the Python reference (which defaults to file-based storage) and the Phoenix adapter (which offers both `EctoStorage` and `FileStorage`), the Rails adapter uses ActiveRecord exclusively because:

* Every Rails app already has a database connection; standing up a parallel JSON-index storage backend duplicates infrastructure for no win.
* ActiveRecord transactions give us atomic `BugReport + Lifecycle` writes for free. The Python `FileStorage` has to fsync an `index.json` after every mutation.
* Multi-node Rails deployments are common; a file-based backend would have the same NFS race-condition caveat the Phoenix `FileStorage` has, and there's no equivalent of a single-node GenServer to serialize writes.

**Screenshots are still flat files on disk** under `BugFab.configuration.storage_root`. Active Storage is intentionally NOT used — Bug-Fab's design rule is "bytes on disk, never in the database." The `bug_fab_bug_reports.screenshot_path` column is the only DB pointer.

If your deployment uses ephemeral local disk (Heroku, Fly Machines without a volume, etc.), mount a persistent volume at `storage_root` or the screenshot bytes disappear on restart. This is a deployment-time concern, not a code-level one — the adapter has no opinion about your storage substrate.

## Asset vendoring (placeholder JS bundle)

The gem ships `app/assets/javascripts/bug_fab/bug-fab.js` as a tiny placeholder that logs a one-line console warning and no-ops the FAB. A real consumer drops the matching upstream `repo/static/bug-fab.js` from the Bug-Fab repo into that path at build/deploy time.

The `bug_fab.assets` initializer in `lib/bug_fab/engine.rb` adds the engine's `app/assets/javascripts` to the host's asset paths and precompiles `bug_fab/bug-fab.js`. This works for Sprockets-based hosts (Rails 7.1 default) out of the box. For Propshaft / esbuild / Vite hosts, reference the file directly from the engine's gem path or vendor it into your own `app/javascript/` tree.

A `Rakefile` task to auto-vendor the bundle that matches `BugFab::PROTOCOL_VERSION` (`bug_fab:vendor_js`) is planned for v0.2. Until then, vendoring is manual.

## Rack status-symbol deprecations (v0.2 cleanup)

Rack 3.x deprecated several status-code symbols in favor of more accurate names:

* `:unprocessable_entity` → `:unprocessable_content` (RFC 9110 renamed 422)
* `:payload_too_large` → `:content_too_large` (RFC 9110 renamed 413)

This adapter renders error responses with **numeric status codes** (`status: 413`, `status: 422`) via `BugFab::Errors::ProtocolError#status`, so it doesn't currently trip the deprecation warnings. The aliases above are flagged here so future contributors don't reintroduce the symbolic forms when refactoring — when Rails 8.1 or later drops the deprecated symbols entirely, no code change should be needed, but a code search for the old names is the safe cleanup pass to schedule for v0.2.

If you wrap the engine's routes in your own controllers and use symbolic statuses, prefer the new names from the outset.

## RuboCop is advisory

`.github/workflows/ci.yml` runs RuboCop with `continue-on-error: true`. The reference adapter intentionally does not block CI on style — the test suite is the contract; lint findings are advisory and reviewed before each release rather than enforced per-commit. Consumers who fork the adapter and want strict linting can flip the flag.

## Testing harness

The test suite uses the canonical Rails Engine pattern: a generated `test/dummy/` host app under `test/dummy/`, `ActiveRecord::Migrator.migrations_paths` extended to include both the dummy's and the engine's migrations, and minitest as the runner. A previous hand-rolled inline `TestApplication` pattern double-evaluated engine routes during `Rails.application.initialize!` and couldn't boot on Rails 7.1 or 7.2 — the dummy-app pattern is the standard convention and is recommended for any downstream adapter forks.

External conformance is the Python pytest plugin (`pip install --pre bug-fab && pytest --bug-fab-conformance --base-url=...`). The inline minitest suite at `test/integration/conformance_test.rb` is a fast in-process smoke; the Python plugin is the authoritative gate.

## GitHub Issues sync

Wired in `lib/bug_fab/github.rb` and gated by `config.github_enabled`. Implementation is **synchronous best-effort** — no Sidekiq / ActiveJob / GoodJob. A GitHub outage logs via `Rails.logger.warn` and returns `nil`; the bug submission still succeeds with a 201 envelope.

If your host has strict latency budgets on the intake endpoint, wrap the call in `Concurrent::Promise` or move the sync invocation into your own background worker via a Rails callback on `BugFab::BugReport` (the model is exposed; the callback hook is not blocked).
