# Rust / Axum adapter — porting notes

Notes specific to the Rust port that don't belong in the public protocol
docs but should travel with this adapter. Promoted to first-party on
2026-05-21.

## Storage trait object pattern

The `Storage` trait is intentionally object-safe so consumers can inject
backends as `Arc<dyn Storage>` rather than threading a generic
`S: Storage` through every handler:

```rust
pub struct AppState {
    pub storage: Arc<dyn storage::Storage>,
    ...
}
```

The trade-off:

* **Pro:** No generic explosion in `AppState`. Consumers can swap
  `FileStorage` ↔ `SqlxStorage` at runtime via a config flag without a
  rebuild.
* **Con:** One indirect call per storage method, plus the `async_trait`
  macro's `Box<dyn Future>` allocation on each method invocation. For the
  Bug-Fab workload (low-frequency intake, moderate viewer reads), this is
  not measurable.

If a future revision needs raw async-fn-in-trait (no `async_trait` macro,
no boxing), the MSRV bumps to 1.75+ — already met — but downstream code
loses some of `async_trait`'s ergonomics around lifetimes. Defer.

## FAB bundle integration

Axum's `serve` accepts a `Router`; the JS frontend bundle is served by
the consumer's existing static-file handler, not by the adapter. If
consumers want a single-binary path:

```rust
use tower_http::services::ServeDir;
let app = axum::Router::new()
    .nest("/api", intake_router(state.clone()))
    .nest("/admin", viewer_router(state))
    .nest_service("/static/bug-fab", ServeDir::new("./static/bug-fab"));
```

Lifetime considerations: `ServeDir` clones internally, so paths can be
owned `PathBuf` without lifetime gymnastics. The bundle itself doesn't
embed; embed via `rust-embed` if a single-binary distribution is
required (out of scope for v0.1).

## `tracing` vs `log`

The adapter uses `tracing` (not `log`) for emit:

* `tracing::error!(error = %e, "save_report failed")` produces structured
  fields that integrate cleanly with the consumer's existing subscriber
  (`tracing-subscriber`, `tracing-bunyan-formatter`, OpenTelemetry).
* `tracing` events are zero-cost when no subscriber is registered, so
  the adapter doesn't force a logging dep on consumers.

If a consumer is on `log`, they can install `tracing-log` once at startup
to bridge `tracing` events into `log`-compatible sinks.

## `chrono` for timestamps

`chrono::Utc::now()` + `to_rfc3339_opts(SecondsFormat::Micros, false)` is
chosen to **match the Python reference's `datetime.now(timezone.utc).isoformat()`
microsecond shape** exactly. If we switched to `time` (the lighter
alternative), the default format omits microseconds and round-tripping
data between Python and Rust adapters surfaces a noisy diff in the
`created_at` field.

The cost is one extra dependency. Worth it for the bit-for-bit
compatibility of stored reports.

## Error handling philosophy

* No `unwrap()` outside `#[cfg(test)]` modules. Every fallible operation
  funnels through `ApiError::into_response()`.
* Storage errors are mapped to 500 by default. The intake-specific
  exceptions (size limit, bad PNG) are surfaced as the documented protocol
  codes (`payload_too_large`, `unsupported_media_type`).
* `ApiError::detail` is a `serde_json::Value` so handlers can embed
  structured detail (e.g., `{"limit_bytes": 10485760}`) without
  stringifying.

## `sqlx` macro caveat

`sqlx::query!` macros require either a live database at compile time or
a checked-in `.sqlx` cache. Rather than force consumers into that
workflow, this adapter uses **runtime-validated** `sqlx::query()` and
`sqlx::query_as()`. The cost is no compile-time type-check of the SQL;
the benefit is the crate compiles without a DB and without bundling
prebuilt query metadata.

If a future revision wants compile-time validation, generate a `.sqlx/`
folder via `cargo sqlx prepare` and commit it. Out of scope for v0.1.

## Why duplicate `payload::build_report` in `storage::sqlx`

`storage::file::FileStorage` keeps its `build_report` as a private
associated function. The sqlx backend reimplements the same logic
inline (~30 LOC duplicated). The duplication is deliberate:

* Both backends test independently — a refactor to one doesn't break
  the other's behavior contract silently.
* The Python reference does the same — `bug_fab/storage/files.py` and
  the SQL backends each carry their own payload builder.

If a third backend lands, refactor to a `payload::build_report` in
`storage/mod.rs`. Two callers ≠ shared abstraction yet.

## Conformance suite

The Python conformance suite (`/conformance` in the upstream repo) can
target this adapter once it's running. Boot `bugfab-example` on
`127.0.0.1:8080` and:

```bash
BUGFAB_TARGET_URL=http://127.0.0.1:8080 \
    pytest --pyargs bug_fab.conformance
```

Conformance-suite gotchas the adapter is _expected_ to need follow-up on:

* The Python reference's `stored_at` returns `bug-fab://reports/{id}`;
  this adapter emits the same string. ✓
* Lifecycle entry `by` field defaults to `"viewer"` for status updates
  here, not `""` like the Python reference. The protocol allows either;
  noted for cross-adapter parity.
* No HTML pages, only JSON. The Python reference's HTML list / detail
  pages are out of scope; the JS bundle queries the JSON routes and
  doesn't need the HTML viewer.

## Crate / file size budget

| Component | Approx LOC |
|-----------|-----------|
| `schemas.rs` | ~260 |
| `storage/mod.rs` | ~110 |
| `storage/file.rs` | ~390 |
| `storage/sqlx.rs` | ~280 |
| `middleware.rs` | ~150 |
| `routes.rs` | ~410 |
| `lib.rs` | ~110 |
| `tests/*` | ~340 |
| `bugfab-example/main.rs` | ~30 |
| **Total** | **~2080 LOC** |

Within the 1500–2500 LOC budget from the brief.
