# bug_fab (Phoenix / Plug adapter)

A mountable Plug router implementing the [Bug-Fab v0.1 wire protocol](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md). Drop it into any Phoenix 1.7+ application (or any pure-Plug Elixir app) and ship in-app bug reports — with screenshot, on-image annotations, and auto-captured browser context — straight from your running app.

> Status: first-party reference adapter for the Elixir/Phoenix ecosystem. Promoted from draft on 2026-05-21 after `mix test` was verified at 37/37 passing under `elixir:1.16`. Hex.pm publish pending tag. Tracked in the Bug-Fab adapters registry: <https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#elixir-phoenix>.

## Install

Add to your `mix.exs`:

```elixir
def deps do
  [
    {:bug_fab, "~> 0.1"},
    # Pick a storage stack:
    # - File storage requires no extra deps.
    # - Postgres-backed Ecto storage:
    {:ecto_sql, "~> 3.11"},
    {:postgrex, "~> 0.17"}
  ]
end
```

Then:

```bash
mix deps.get
mix ecto.migrate    # only if using EctoStorage
```

## Mount

In a Phoenix endpoint (`lib/my_app_web/endpoint.ex`):

```elixir
defmodule MyAppWeb.Endpoint do
  use Phoenix.Endpoint, otp_app: :my_app

  # ... existing plugs ...

  forward "/api", BugFab.IntakeRouter
  forward "/admin/bug-reports", BugFab.ViewerRouter
end
```

Or in a standalone `Plug.Cowboy` application (see `examples/minimal/`):

```elixir
{Plug.Cowboy, scheme: :http, plug: BugFab.IntakeRouter, options: [port: 4000]}
```

Both routers honor the wire protocol's mount-prefix invariant: any non-empty mount path works.

## Auth

Bug-Fab v0.1 ships no auth abstraction. You wire auth at the mount point using your existing pipelines:

```elixir
scope "/admin", MyAppWeb do
  pipe_through [:browser, :require_admin]
  forward "/bug-reports", BugFab.ViewerRouter
end
```

For per-action gating without changing auth middleware, configure `:viewer_permissions` (see below).

See [`docs/PROTOCOL.md` § Auth — mount-point delegation](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md#auth--mount-point-delegation) for the design rationale.

## Configure

```elixir
# config/config.exs (or config/runtime.exs for prod)
import Config

config :bug_fab,
  # Storage — file-based default works for hobby / single-node deployments.
  storage: {BugFab.Storage.FileStorage,
            [storage_dir: "/var/lib/bug-fab", id_prefix: ""]},

  # Or Ecto, supervised by your host application's Repo:
  # storage: {BugFab.Storage.EctoStorage,
  #           [repo: MyApp.Repo, screenshot_dir: "/var/lib/bug-fab/images"]},

  # 4 MiB cap — protocol allows up to 10 MiB; tighten to match your infra.
  max_upload_mb: 4,

  # Per-IP rate limit. Off by default — flip to true to enforce.
  rate_limit_enabled: false,
  rate_limit_max: 30,
  rate_limit_window_seconds: 60,

  # Optional id prefix for multi-environment shared collectors:
  # "P" -> bug-P001, bug-P002 ...  "D" -> bug-D001 ...
  id_prefix: "",

  # Per-route gates. Disable a flag to return 403 with the protocol envelope.
  viewer_permissions: %{
    can_edit_status: true,
    can_delete: true,
    can_bulk: true
  },

  # Optional: surface the host's authenticated user as the lifecycle "by" field.
  # The function receives the Plug.Conn and returns a string or nil.
  actor_resolver: fn conn -> get_session(conn, :user_email) end
```

### Runtime env vars

For deployments that prefer `config/runtime.exs`:

| Env var | Maps to | Default |
|---------|---------|---------|
| `BUG_FAB_STORAGE_DIR` | `FileStorage[:storage_dir]` | `<tmp>/bug-fab` |
| `BUG_FAB_SCREENSHOT_DIR` | `EctoStorage[:screenshot_dir]` | (required) |
| `BUG_FAB_MAX_UPLOAD_MB` | `:max_upload_mb` | `4` |
| `BUG_FAB_RATE_LIMIT_ENABLED` | `:rate_limit_enabled` | `false` |
| `BUG_FAB_RATE_LIMIT_MAX` | `:rate_limit_max` | `30` |
| `BUG_FAB_RATE_LIMIT_WINDOW` | `:rate_limit_window_seconds` | `60` |
| `BUG_FAB_ID_PREFIX` | `:id_prefix` | `""` |

Wire these up in `config/runtime.exs`:

```elixir
config :bug_fab,
  storage: {BugFab.Storage.FileStorage,
            [storage_dir: System.get_env("BUG_FAB_STORAGE_DIR") || "/var/lib/bug-fab"]},
  max_upload_mb: String.to_integer(System.get_env("BUG_FAB_MAX_UPLOAD_MB") || "4"),
  rate_limit_enabled: System.get_env("BUG_FAB_RATE_LIMIT_ENABLED") == "true"
```

## Endpoints

All eight v0.1 endpoints. Paths below assume `forward "/api", BugFab.IntakeRouter` and `forward "/admin/bug-reports", BugFab.ViewerRouter`.

| Method | Path | Purpose |
|--------|------|---------|
| POST   | `/api/bug-reports`                          | Submit a report (multipart) |
| GET    | `/admin/bug-reports/`                       | Minimal HTML viewer index |
| GET    | `/admin/bug-reports/reports`                | JSON list with filters + pagination |
| GET    | `/admin/bug-reports/reports/:id`            | JSON detail |
| GET    | `/admin/bug-reports/reports/:id/screenshot` | Raw PNG bytes |
| PUT    | `/admin/bug-reports/reports/:id/status`     | Update status, append lifecycle |
| DELETE | `/admin/bug-reports/reports/:id`            | Hard delete |
| POST   | `/admin/bug-reports/bulk-close-fixed`       | Bulk close all `fixed` |
| POST   | `/admin/bug-reports/bulk-archive-closed`    | Bulk archive all `closed` |

## Frontend bundle

Reference the Bug-Fab JS bundle from your application layout (`lib/my_app_web/components/layouts/root.html.heex`):

```heex
<script src={~p"/bug-fab/bug-fab.js"} defer></script>
```

You'll need to serve the bundle from `priv/static/` (Phoenix) or `priv/static/bug-fab/` (Plug). Drop the matching upstream bundle from <https://github.com/AZgeekster/Bug-Fab/blob/main/static/bug-fab.js> into your asset pipeline.

## Storage backends

### FileStorage (default)

JSON on disk + atomic tmp+rename writes. Zero external dependencies. Single-node only — see `MIGRATION_NOTES.md` § "Multi-node FileStorage caveat".

### EctoStorage

Schema lives in `priv/repo/migrations/*_create_bug_reports.exs`. Postgres-first, SQLite via `ecto_sqlite3` also supported. Screenshots are stored on disk (NOT as DB blobs) at `:screenshot_dir` to avoid TOAST bloat in Postgres / oversized-cell pressure in SQLite.

Migration runs via your host application's Repo:

```bash
mix ecto.migrate
```

## Tests

```bash
mix deps.get
mix test
```

Covers: schema validation (422 on bad severity), magic-byte rejection (415 on non-PNG), size cap (413), rate limiting (429), file-storage roundtrip, status updates with lifecycle, bulk operations.

## Limitations (v0.1)

* HTML viewer is minimal — real consumers will typically replace it with a Phoenix LiveView page. The JSON API endpoints cover every interaction a LiveView needs. See `MIGRATION_NOTES.md` § "LiveView viewer".
* `FileStorage` is single-node. Multi-node deployments must use `EctoStorage`.
* GitHub Issues sync is not yet wired in this adapter — see `MIGRATION_NOTES.md`.
