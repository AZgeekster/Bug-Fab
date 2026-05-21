# Bug-Fab Laravel Adapter — Migration & Operational Notes

Notes for adopters and future maintainers. Captures the trade-offs that aren't visible from reading the source alone.

## Eloquent vs FileStorage

The package ships two `StorageContract` implementations.

| Concern | FileStorage | EloquentStorage |
| --- | --- | --- |
| Deps | Filesystem disk only | DB + filesystem disk |
| Worker safety | Single-worker only | Multi-worker safe (transactions + `lockForUpdate`) |
| Octane compatibility | Risky (PHP file APIs are sync but state isn't shared) | Yes |
| Multi-host | No (per-host disk) | Yes (shared DB, plus shared disk for screenshots — e.g., S3) |
| Test ergonomics | Drop-in, no migrations | Run migrations in `RefreshDatabase` |
| Migration into the package | Nothing (port from FastAPI ref) | Eloquent models = framework idiomatic |

Default is `eloquent` because the realistic Laravel deployment is `php artisan serve` for dev and php-fpm with N workers for prod. The file backend is for hobby / demo / single-developer single-node use.

### Switching backends

Pick at app boot via `BUG_FAB_STORAGE` env. The service provider binds the container singleton once per request lifecycle, so the choice is global — but `config(['bugfab.storage' => 'file'])` followed by re-resolving the container will work for tests that need to switch.

Reports written with one backend are NOT readable by the other backend — the on-disk JSON layout and the DB row layout are different. Migration scripts between them are out of scope for v0.1.

## Octane considerations

Laravel Octane (Swoole / RoadRunner / FrankenPHP) keeps the application instance alive across requests. Two things to watch:

1. **Request-scoped state must not leak.** This adapter holds no per-request state on the service provider or controllers — every request resolves fresh state from `StorageContract` and the request itself. Safe.
2. **The `StorageContract` binding is a singleton.** Both backends are stateless beyond their constructor args (disk, path, ID prefix). Octane reusing the same instance across requests is fine — there's no shared mutable buffer.

Octane users should still:
- Confirm their MySQL connection pool isn't exhausted by the bulk endpoints (they use `lockForUpdate` in a transaction, holding row locks until commit).
- Tune `octane.workers` × `BUG_FAB_RATE_LIMIT_MAX` so the per-worker counters don't multiply the effective limit. (Laravel's `RateLimiter` uses the cache store, so the count is shared — but only if the cache driver is shared like Redis. The default `array` cache resets per worker.)

## Queue / scheduler considerations

v0.1 has no async work — GitHub sync is stubbed and webhook delivery isn't wired through this adapter yet. Future versions will likely:

- Move GitHub sync to a queued job (`ShouldQueue` with a small retry policy). Failures need a DLQ pattern — Laravel's `failed_jobs` table is the right home for this.
- Schedule a daily `bug-fab:archive-stale` artisan command via `routes/console.php`.

Consumers wiring Bug-Fab into apps with non-default queue connections should note: the package doesn't auto-bind any queue connection. When v0.2 lands with queued GitHub sync, the connection will resolve via `config('queue.default')`.

## Rate-limit semantics

The `RateLimiter` facade backs onto the cache store. If your cache driver is `array` (default in dev), each php-fpm worker has its own counter and the effective limit is N × workers. Use `redis` or `database` cache for the rate limiter to actually limit anything in multi-worker setups.

## CSRF and the intake endpoint

`POST /bug-reports` is exempt from CSRF because the frontend bundle submits multipart from whatever page the user is on, without participating in Laravel's session-CSRF flow. The default middleware group for intake is `api` (no CSRF). If you change `bugfab.routes.intake.middleware` to include `web`, also add `bug-reports` to your `VerifyCsrfToken` `$except` list — or the intake will 419.

Mount-point auth is the line of defense per `PROTOCOL.md` § Auth.

## Form-request validation vs `Validator::extend`

This adapter uses PHP-native enums (`Severity`, `Status`, `ReportType`) plus Laravel's built-in `Rule::enum()` rule for write-side strictness. The anti-pattern would be reaching for `Validator::extend('severity', ...)` — that approach:

- Skips IDE-level type information on the enum class.
- Duplicates the enum vocabulary in a Closure that needs to be hand-synced when the protocol evolves.
- Doesn't surface in `php artisan about` / OpenAPI generators.

Rule::enum() pulls the allowed cases directly off the enum, so adding a new severity is a one-line change in `src/Enums/Severity.php`.

## Service provider lifecycle (do's and don'ts)

The Bug-Fab service provider follows Laravel's contract precisely:

- `register()` only calls `mergeConfigFrom` and `singleton` (with a **closure** that constructs the storage lazily). No DB connections, no disk handles, no Eloquent queries.
- `boot()` calls `loadRoutesFrom`, `loadViewsFrom`, `loadMigrationsFrom`, `publishes`. Still no DB queries.

The first DB query happens when a request comes in and the controller resolves `StorageContract` from the container. This keeps `php artisan list` fast and works correctly during package discovery (where the DB may not exist yet).

## Future work

- `AuthAdapter` ABC (v0.2) — currently `viewer_actor()` is best-effort, pulling `email` / `name` / `getAuthIdentifier()` off `$request->user()`.
- GitHub Issues sync (currently stubbed — the field is always `null` in the intake response).
- Idempotency keys on intake (deferred to v0.2 by the protocol itself).
- A `BugFabActor` middleware shim that consumers can register to surface a richer actor string on the lifecycle log.
