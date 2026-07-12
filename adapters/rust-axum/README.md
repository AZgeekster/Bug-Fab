# Bug-Fab — Rust / Axum adapter

First-party Rust adapter for the Bug-Fab v0.1 wire protocol, built on
Axum 0.7 + Tokio 1.

> **Status:** first-party reference adapter for the Rust ecosystem.
> Promoted from draft on 2026-05-21 after `cargo test --workspace` was
> verified at 21/21 passing under `rust:1.75`. Not yet published to
> crates.io — install via path or git dep until the first tagged release.

## Layout

```
rust-axum/
├── Cargo.toml             workspace root
├── bugfab/                library crate
│   ├── Cargo.toml
│   ├── src/
│   │   ├── lib.rs         AppState, Settings, router builders
│   │   ├── routes.rs      eight protocol handlers + ApiError envelope
│   │   ├── schemas.rs     wire types (BugReportCreate, Status, ...)
│   │   ├── middleware.rs  body limit, per-IP rate limiter, PNG check
│   │   └── storage/
│   │       ├── mod.rs     Storage trait, ListFilters, StorageError
│   │       ├── file.rs    FileStorage — JSON on disk + index.json
│   │       └── sqlx.rs    SqlxStorage — SQLite, behind `sqlx` feature
│   └── tests/
│       ├── integration.rs HTTP-level tests via tower::oneshot
│       └── storage_roundtrip.rs
├── bugfab-example/        example binary consumer (under 50 LOC)
│   ├── Cargo.toml
│   └── src/main.rs
└── MIGRATION_NOTES.md
```

## Install / wire-up

```rust
use std::sync::Arc;
use std::net::SocketAddr;
use bugfab::{intake_router, viewer_router, AppState, Settings};
use bugfab::storage::file::FileStorage;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let storage = Arc::new(FileStorage::new("./bug-fab-data", "")?);
    let state = Arc::new(AppState::new(storage, Settings::default()));

    // Mount intake openly under /api, viewer behind your admin auth.
    let app = axum::Router::new()
        .nest("/api", intake_router(state.clone()))
        .nest("/admin", viewer_router(state));

    let addr: SocketAddr = "0.0.0.0:8080".parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    ).await?;
    Ok(())
}
```

`into_make_service_with_connect_info::<SocketAddr>()` is mandatory if
you want the per-IP rate limiter to see the peer address. Without it,
`ConnectInfo` won't be available and the limiter keys on "unknown".

`X-Forwarded-For` is client-controlled and spoofable, so the limiter
honors it only when the direct peer is listed in
`Settings.rate_limit_trusted_proxies` (`"*"` trusts every peer). Empty —
the secure default — meters by the direct peer address, so list your
reverse-proxy IPs to restore per-end-user metering behind a proxy. Idle
buckets are evicted once per window, so the bucket map stays bounded.

## Feature flags

| Feature | Default | Effect |
|---------|---------|--------|
| _(none)_ | ✅ | File-backed storage only (`FileStorage`). Zero non-stdlib runtime deps beyond axum/serde. |
| `sqlx` | ❌ | Adds `SqlxStorage` (SQLite via `sqlx::sqlite`). Compiles in `sqlx` macros and the SQLite driver. |

To enable the sqlx backend in a consumer:

```toml
bugfab = { path = "...", features = ["sqlx"] }
```

Then:

```rust
let storage = Arc::new(
    bugfab::storage::sqlx::SqlxStorage::connect(
        "sqlite:bugfab.sqlite?mode=rwc",
        "",
    ).await?,
);
```

The schema is auto-bootstrapped on first connect (see
`[[auto_init_storage_schema]]` in the auto-memory).

## Storage backend matrix

| Backend | Process model | Storage location | Notes |
|---------|---------------|------------------|-------|
| `FileStorage` | Single process (in-memory mutex) | Local FS | Mirrors the Python reference's on-disk layout exactly. |
| `SqlxStorage` | Multi-worker safe (connection pool) | SQLite file or `:memory:` | Survives concurrent writes; payload kept as JSON to preserve extra context keys verbatim. |

## Protocol coverage

All eight endpoints from `docs/PROTOCOL.md` are implemented:

| Method | Path | Handler |
|--------|------|---------|
| POST | `/bug-reports` | `routes::submit` |
| GET | `/reports` | `routes::list_reports` |
| GET | `/reports/{id}` | `routes::get_report` |
| GET | `/reports/{id}/screenshot` | `routes::get_screenshot` |
| PUT | `/reports/{id}/status` | `routes::update_status` |
| DELETE | `/reports/{id}` | `routes::delete_report` |
| POST | `/bulk-close-fixed` | `routes::bulk_close_fixed` |
| POST | `/bulk-archive-closed` | `routes::bulk_archive_closed` |

Validation:

* `protocol_version != "0.1"` → 400 `unsupported_protocol_version`
* Invalid `severity` / `status` / `report_type` → 422 `schema_error`
* Missing multipart parts or malformed JSON → 400 `validation_error`
* Screenshot not PNG (magic byte mismatch) → 415 `unsupported_media_type`
* Screenshot over `max_screenshot_bytes` → 413 `payload_too_large` with `limit_bytes`
* Per-IP rate limit exceeded → 429 `rate_limited` with `retry_after_seconds`
* Permission flag off → 403 `forbidden`

## Tests

```bash
cargo test --all                        # default feature set
cargo test --all --features sqlx        # with sqlx backend compiled in
```

Coverage:

* `schemas` — enum validation + extra-key preservation
* `middleware` — token bucket admit/reject + PNG magic byte check
* `tests/integration.rs` — every endpoint end-to-end via `tower::oneshot`
* `tests/storage_roundtrip.rs` — `FileStorage` save/list/update/delete

## Conformance

Cross-stack conformance is wired via `conformance/run-conformance.sh`, which
boots `bugfab-example` in a `rust:1.75` container and runs the upstream
`pytest --bug-fab-conformance` suite against it from a sibling
`python:3.12-slim` container. See [`conformance/README.md`](conformance/README.md).

| Date | Result | Runtime image |
|------|--------|---------------|
| 2026-05-21 | **30 / 30 passed** in 1.13 s (pytest) | `rust:1.75` + `python:3.12-slim` |

## MSRV

| Feature set | Required rustc |
|-------------|----------------|
| Default (file storage only) | **1.75** |
| `sqlx` feature enabled | **1.86** |

The bump for `sqlx` is forced by transitive deps (`icu_*`, `idna_adapter`,
`home`) that adopted edition2024 in 2026. Default-feature builds stay on
the 1.75 MSRV — adopting `sqlx` is the consumer's tradeoff.

Bumping the default MSRV is a breaking change for downstream consumers,
so any future bump goes through the adapter's deprecation policy (see
`docs/decisions.md` upstream).

## Async runtime

Tokio multi-thread, current_thread, or any runtime providing `spawn`
plus `tokio::sync::Mutex` semantics. The adapter only uses `tokio::fs`,
`tokio::sync`, and Axum's transport, so there's no hard dependency on a
specific scheduler tier.

## Out of scope (v0.1)

The v0.1 release of this adapter does **not** include:

* GitHub Issues sync (the Python reference has it; not yet ported)
* Generic webhook delivery
* PII redaction
* CSP-nonce injection for any HTML viewer (this adapter is JSON-only)
* HTML viewer templates — the JSON viewer endpoints suffice for the
  drop-in JS bundle; consumers wanting server-rendered HTML can write
  their own with `askama` or `minijinja` on top of `viewer_router`.

These are all candidates for v0.1 follow-up once the wire-protocol port
is proven against the upstream conformance suite.
