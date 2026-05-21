# Bug-Fab Vapor adapter

> **Status:** First-party adapter — promoted 2026-05-21. Verified 9/9 tests passing under `swift:6.0` (Vapor 4.92+). See `MIGRATION_NOTES.md` for the Swift 6 strict-concurrency migration story.

A Swift / Vapor 4 adapter for the [Bug-Fab](https://github.com/AZgeekster/Bug-Fab) wire protocol (v0.1).

## What you get

- `BugFab` library — eight protocol routes wired through a `BugFabStorage` protocol.
- `BugFabFileStorage` — on-disk JSON + PNG layout matching the Python reference implementation byte-for-byte (same `index.json` shape, same `archive/` subdir).
- `BugFabFluentStorage` — Fluent ORM model + migration. Tested against SQLite (`fluent-sqlite-driver`) in-memory; Postgres is the documented production driver (`fluent-postgres-driver`).
- Strict severity / status enums via `Codable` + manual decode validation. Unknown values → `422 schema_error`.
- 4 MiB screenshot cap (configurable). PNG magic-byte verification. Non-PNG → `415`.
- Optional per-IP rate limiter, off by default. Hit → `429` with `retry_after_seconds`.
- `XCTVapor` test harness covering happy path, validation, bulk ops, rate limit, and the Fluent backend.

## Add to your project

Requires Swift 6.0+ (Vapor 4 toolchain).

```swift
// Package.swift
dependencies: [
    .package(url: "https://github.com/<your-org>/bug-fab-vapor.git", from: "0.1.0"),
    // Bring the driver you actually plan to use:
    .package(url: "https://github.com/vapor/fluent-postgres-driver.git", from: "2.8.0"),
],
targets: [
    .executableTarget(
        name: "MyApp",
        dependencies: [
            .product(name: "BugFab", package: "bug-fab-vapor"),
            .product(name: "FluentPostgresDriver", package: "fluent-postgres-driver"),
        ]
    )
]
```

## Wire it up

```swift
import BugFab
import Vapor

public func configure(_ app: Application) throws {
    // Choose a storage backend.
    let storage = try BugFabFileStorage(
        storageDirectory: URL(fileURLWithPath: "/var/lib/bug-fab")
    )

    // Or, for Fluent:
    // app.databases.use(.postgres(...), as: .psql)
    // app.migrations.add(CreateBugFabReport())
    // try app.autoMigrate().wait()
    // let storage = BugFabFluentStorage(app: app)

    try app.bugFab(storage: storage)

    // Mount intake and viewer separately so different auth layers can wrap
    // each one. (Public submit endpoint, admin-only viewer is the typical
    // pattern.)
    try BugFab.intakeRoutes(app.grouped("api"))
    try BugFab.viewerRoutes(app.grouped("admin")
        .grouped(MyAdminAuthMiddleware()))
}
```

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| POST | `/bug-reports` | multipart intake: `metadata` (JSON string) + `screenshot` (PNG file) |
| GET | `/reports` | filterable list with stat counts |
| GET | `/reports/{id}` | full detail payload |
| GET | `/reports/{id}/screenshot` | raw `image/png` bytes |
| PUT | `/reports/{id}/status` | append status change to lifecycle log |
| DELETE | `/reports/{id}` | hard delete |
| POST | `/bulk-close-fixed` | transition every `fixed` to `closed` |
| POST | `/bulk-archive-closed` | move every `closed` to archive |

All eight follow `repo/docs/PROTOCOL.md` (v0.1).

## Configuration

`BugFabSettings` can be constructed directly or via `BugFabSettings.fromEnvironment(_:)`.

| Env var | `BugFabSettings` property | Default | Notes |
|---------|--------------------------|---------|-------|
| `BUG_FAB_MAX_UPLOAD_MB` | `maxUploadBytes` | 4 MiB | Screenshot cap. Set in MiB on the env, bytes on the struct. |
| `BUG_FAB_PROTOCOL_VERSION` | `protocolVersion` | `"0.1"` | Reject submissions whose `protocol_version` differs. |
| `BUG_FAB_RATE_LIMIT_ENABLED` | `rateLimitEnabled` | `false` | Toggle the per-IP limiter. |
| `BUG_FAB_RATE_LIMIT_MAX` | `rateLimitMax` | `30` | Requests per window per IP. |
| `BUG_FAB_RATE_LIMIT_WINDOW_SECONDS` | `rateLimitWindowSeconds` | `60` | Sliding window. |
| `BUG_FAB_ID_PREFIX` | `idPrefix` | `""` | Optional `P` / `D` style env prefix on assigned IDs. |
| `BUG_FAB_CAN_EDIT_STATUS` | `canEditStatus` | `true` | Disables `PUT /reports/{id}/status` when false (403). |
| `BUG_FAB_CAN_DELETE` | `canDelete` | `true` | Disables `DELETE /reports/{id}`. |
| `BUG_FAB_CAN_BULK` | `canBulk` | `true` | Disables both bulk endpoints. |

## Running locally

```bash
swift run BugFabExample
# In another terminal:
curl -X POST http://localhost:8080/api/bug-reports \
  -F 'metadata={"protocol_version":"0.1","title":"hi","client_ts":"2026-04-27T00:00:00Z","context":{"environment":"dev"}};type=application/json' \
  -F 'screenshot=@/path/to/shot.png;type=image/png'
```

## Tests

```bash
swift test
```

Covers:

- Multipart happy path → list + screenshot fetch
- Severity / status enum rejection → 422
- Non-PNG body → 415
- Oversized payload → 413 with `limit_bytes`
- Missing / unknown `protocol_version` → 400 with the matching error code
- Bulk close + archive round-trip
- Rate limit hit → 429 with `retry_after_seconds`
- `BugFabFluentStorage` round-trip on in-memory SQLite

## Notes for adapter authors

See `MIGRATION_NOTES.md` for Swift-specific gotchas — `async/await` ergonomics, `EventLoop` vs `Task.detached`, Fluent property-wrapper subtleties, and how strict `Codable` validation interacts with the read-side deprecated-values rule.
