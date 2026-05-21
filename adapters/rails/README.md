# bug_fab-rails

A mountable Rails Engine that implements the [Bug-Fab v0.1 wire protocol](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md). Drop it into any Rails 7.1+ application and ship in-app bug reports — with screenshot, on-image annotations, and auto-captured browser context — straight from your running app.

> Status: first-party reference adapter for the Rails ecosystem. Promoted from draft on 2026-05-21 after `bundle exec rake test` was verified at 22/22 passing (107 assertions) under `ruby:3.3`. Tracked in the Bug-Fab adapters registry: <https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#ruby-on-rails>.

## Install

Add to your Gemfile:

```ruby
gem "bug_fab-rails"
```

Then:

```bash
bundle install
bin/rails generate bug_fab:install
bin/rails db:migrate
```

The install generator creates `config/initializers/bug_fab.rb` with every option documented. The engine's migrations are appended to your application's migration paths automatically — no separate copy step needed.

## Mount

Add to `config/routes.rb`:

```ruby
Rails.application.routes.draw do
  # ...
  mount BugFab::Engine, at: "/bug-fab"
end
```

The mount prefix is arbitrary, but it MUST be non-empty — the viewer's HTML index serves at the mount root per the protocol's mount-prefix invariant.

## Auth

Bug-Fab v0.1 ships no auth abstraction. You wire auth at the mount point using your existing middleware:

```ruby
authenticate :user, ->(u) { u.admin? } do
  mount BugFab::Engine, at: "/admin/bug-fab"
end
```

Or split intake (open) from viewer (admin-only) by mounting twice with route constraints. See [`docs/PROTOCOL.md` § Auth — mount-point delegation](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md#auth--mount-point-delegation).

## Configure

```ruby
# config/initializers/bug_fab.rb
BugFab.configure do |config|
  # Where screenshot bytes live on disk. NEVER stored as DB blobs.
  config.storage_root = Rails.root.join("storage", "bug-fab")

  # 10 MiB cap matches the protocol default.
  config.max_upload_mb = 10

  # Per-route gates. Disable a flag to return 403 with the protocol envelope.
  config.viewer_permissions = {
    can_edit_status: true,
    can_delete: true,
    can_bulk: true
  }

  # Optional: surface the host's authenticated user as the lifecycle "by" field.
  config.actor_resolver = ->(request) { request.env["warden"]&.user&.email }

  # Optional: GitHub Issues sync. Best-effort — failures log, never raise.
  config.github_enabled = ENV["BUG_FAB_GITHUB_ENABLED"] == "true"
  config.github_pat     = ENV["BUG_FAB_GITHUB_PAT"]
  config.github_repo    = ENV["BUG_FAB_GITHUB_REPO"] # "owner/repo"
end
```

## Frontend bundle

Reference the Bug-Fab JS bundle from your application layout:

```erb
<%= javascript_include_tag "bug_fab/bug-fab" %>
```

The gem vendors the upstream `bug-fab.js` bundle (replace with your build process before deploy). Drop the matching upstream bundle from `https://github.com/AZgeekster/Bug-Fab/blob/main/static/bug-fab.js` into `app/assets/javascripts/bug_fab/bug-fab.js` (or vendor it via your own asset pipeline) to enable the FAB.

## Endpoints

All eight v0.1 endpoints, mounted relative to the engine mount point (`/bug-fab` in the examples below):

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/bug-fab/`                          | HTML viewer index |
| GET    | `/bug-fab/:id`                       | HTML viewer detail page |
| POST   | `/bug-fab/bug-reports`               | Submit a report (multipart) |
| GET    | `/bug-fab/reports`                   | JSON list with filters + pagination |
| GET    | `/bug-fab/reports/:id`               | JSON detail |
| GET    | `/bug-fab/reports/:id/screenshot`    | Raw PNG bytes |
| PUT    | `/bug-fab/reports/:id/status`        | Update status, append lifecycle |
| DELETE | `/bug-fab/reports/:id`               | Hard delete |
| POST   | `/bug-fab/bulk-close-fixed`          | Close every fixed report |
| POST   | `/bug-fab/bulk-archive-closed`       | Archive every closed report |

## Storage

- **Screenshots** live on disk under `BugFab.configuration.storage_root` as `bug-NNN.png`. Active Storage is intentionally NOT used — Bug-Fab's design rule is "bytes on disk, never in the database."
- **Metadata + lifecycle** live in two new tables: `bug_fab_bug_reports` and `bug_fab_lifecycle_events`. A `bug_fab_id_counter` table mints sequential IDs portably across SQLite, Postgres, and MySQL.

## Conformance

Adapter conformance is verified by the Python pytest plugin shipped with the `bug-fab` package. Two paths:

### One-command Docker harness (recommended)

```bash
cd conformance
./run-conformance.sh
```

Boots `ruby:3.3` + the in-repo `test/dummy/` app, runs the pytest plugin from a sibling `python:3.12-slim` container, and writes the transcript to `conformance/out/conformance-results.txt`. No Ruby or Python install on the host. See [`conformance/README.md`](conformance/README.md) for the full breakdown of how the harness applies a runtime `storage_root` + `DATABASE_URL` override without touching the dummy app on disk.

Latest green run: **30/30** passed under `mount BugFab::Engine => "/bug-fab"`.

### Manual (host-installed Ruby + Python)

```bash
pip install --pre bug-fab
bin/rails server &
pytest --bug-fab-conformance --base-url=http://localhost:3000/bug-fab
```

### Inline minitest smoke

Ships an inline minitest conformance smoke test at `test/integration/conformance_test.rb` that exercises all 8 endpoints in-process. Run via `bundle exec rake test`.

## Development

```bash
bundle install
bundle exec rake test
bundle exec rubocop
```

## License

MIT — see `LICENSE.txt`.
