# Bug-Fab Laravel Adapter

Laravel 11 / PHP 8.3 adapter implementing the [Bug-Fab v0.1 wire protocol](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md).

Drop-in: `composer require bugfab/laravel-adapter`, run `php artisan migrate`, and `POST /api/bug-reports` is live. Pair with the Bug-Fab JS frontend bundle (separate package) to get the floating action button + screenshot + annotation overlay.

## Install

```bash
composer require bugfab/laravel-adapter
php artisan vendor:publish --tag=bugfab-config   # optional — only if you want to edit defaults
php artisan migrate                              # only if BUG_FAB_STORAGE=eloquent (default)
```

The service provider is auto-registered via Laravel package discovery — no manual edits to `config/app.php`.

## Endpoints

Eight routes, mounted under two configurable prefixes. Defaults:

| Method | Path | Purpose |
| --- | --- | --- |
| POST   | `/api/bug-reports` | Submit a report (intake) |
| GET    | `/admin/bug-reports/` | HTML list view |
| GET    | `/admin/bug-reports/reports` | JSON list |
| GET    | `/admin/bug-reports/reports/{id}` | JSON detail |
| GET    | `/admin/bug-reports/{id}` | HTML detail page |
| GET    | `/admin/bug-reports/reports/{id}/screenshot` | Raw PNG |
| PUT    | `/admin/bug-reports/reports/{id}/status` | Update status |
| DELETE | `/admin/bug-reports/reports/{id}` | Hard delete |
| POST   | `/admin/bug-reports/bulk-close-fixed` | Bulk close |
| POST   | `/admin/bug-reports/bulk-archive-closed` | Bulk archive |

Mount-point auth (per `PROTOCOL.md` § Auth) is the gate — wrap the viewer prefix with your existing admin middleware.

## Storage backends

| Backend | When | Caveats |
| --- | --- | --- |
| `eloquent` (default) | Any production deployment. MySQL / MariaDB / PostgreSQL. SQLite in tests. | Run `php artisan migrate`. Multi-worker safe. |
| `file` | Single-node hobby projects and demos. | Not multi-worker safe — no cross-process locking. Use Eloquent in Octane / php-fpm with workers > 1. |

Switch via `BUG_FAB_STORAGE=file` in `.env`.

## Configuration

All env vars listed below default to safe values; the table is for tuning.

| Env var | Default | Notes |
| --- | --- | --- |
| `BUG_FAB_STORAGE` | `eloquent` | `file` or `eloquent`. |
| `BUG_FAB_DB_CONNECTION` | (app default) | Use a separate audit DB by name. |
| `BUG_FAB_FILE_DISK` | `local` | Filesystem disk for `file` storage. |
| `BUG_FAB_FILE_PATH` | `bug-fab` | Path prefix under the disk root. |
| `BUG_FAB_SCREENSHOT_DISK` | `local` | Where Eloquent writes the PNG blobs. |
| `BUG_FAB_SCREENSHOT_PATH` | `bug-fab/screenshots` | Relative path under the disk root. |
| `BUG_FAB_ID_PREFIX` | (empty) | One-letter prefix for shared collectors (`P` → `bug-P001`). |
| `BUG_FAB_MAX_SCREENSHOT_MB` | `4` | Hard cap on screenshot size. 413 with `limit_bytes` when exceeded. |
| `BUG_FAB_RATE_LIMIT_ENABLED` | `false` | Enable per-IP rate limiting. |
| `BUG_FAB_RATE_LIMIT_MAX` | `10` | Allowed submissions per window. |
| `BUG_FAB_RATE_LIMIT_WINDOW` | `60` | Window seconds. |
| `BUG_FAB_INTAKE_PREFIX` | `api` | Path prefix for `POST /bug-reports`. |
| `BUG_FAB_VIEWER_PREFIX` | `admin/bug-reports` | Path prefix for the viewer. |
| `BUG_FAB_VIEWER_CAN_EDIT_STATUS` | `true` | Mount `PUT /status` route at all. |
| `BUG_FAB_VIEWER_CAN_DELETE` | `true` | Mount `DELETE /{id}` route at all. |
| `BUG_FAB_VIEWER_CAN_BULK` | `true` | Mount bulk routes at all. |
| `BUG_FAB_VIEWER_PAGE_SIZE` | `20` | Default list page size (max 200). |
| `BUG_FAB_CSP_NONCE_HEADER` | (none) | Header name to read CSP nonces from. |
| `BUG_FAB_GITHUB_ENABLED` | `false` | Reserved — GitHub Issues sync is a no-op in v0.1; wiring lands in v0.2. |

## Conformance status

Implements the v0.1 wire protocol surface end-to-end. Tested against the protocol's documented behaviors:

- All 8 endpoints with the correct HTTP methods and status codes.
- Strict severity enum via PHP-native `Rule::enum()` (no `Validator::extend`).
- Strict status enum on write; lenient on read per the deprecated-values rule.
- Magic-byte PNG verification before persistence (Content-Type alone is not trusted).
- 4 MiB screenshot cap (configurable). 413 includes `limit_bytes`.
- `protocol_version` `"0.1"` required; mismatch → `400 unsupported_protocol_version`.
- Server User-Agent captured from request header independently of any client-supplied value.
- Lifecycle audit log: append-only, with `created` on submit and `status_changed` on every PUT.
- Path-traversal guard on `{id}` route param (regex constrained at route + storage layers).
- Per-IP rate limiting via `RateLimiter` facade (off by default).
- File + Eloquent backends round-trip identically against the same endpoints.

Not yet covered (out of v0.1 scope per `PROTOCOL.md`): `AuthAdapter` ABC, `Idempotency-Key`, GitHub Issues sync (stubbed). See `MIGRATION_NOTES.md` for trade-offs.

## Testing

```bash
composer install
vendor/bin/phpunit
```

Tests use orchestra/testbench with in-memory SQLite and a fake `local` disk — no external services required.

## License

MIT.
