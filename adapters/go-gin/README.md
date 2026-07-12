# bugfab-gin

Go + [Gin](https://github.com/gin-gonic/gin) adapter for the [Bug-Fab](https://github.com/AZgeekster/Bug-Fab) wire protocol v0.1.

> **Status:** first-party reference adapter for the Go ecosystem. Promoted from draft on 2026-05-21 after `go test ./...` was verified at 37/37 passing (75.7% statement coverage) and the upstream `pytest --bug-fab-conformance` suite passed 30/30 against the example server (see [`conformance/`](./conformance/)). Tracks Bug-Fab protocol v0.1.

This package wires the eight Bug-Fab endpoints onto any Gin engine, with a default file-based storage backend that mirrors the Python reference's on-disk layout byte-for-byte. A report written by this adapter can be read by the Python reference (and vice versa) without conversion.

---

## Install

```sh
go get github.com/AZgeekster/Bug-Fab/adapters/go-gin/bugfab
```

Requires Go 1.22+ and Gin v1.10+.

## Quickstart

```go
package main

import (
    "log"

    "github.com/AZgeekster/Bug-Fab/adapters/go-gin/bugfab"

    "github.com/gin-gonic/gin"
)

func main() {
    cfg := bugfab.NewConfigFromEnv()
    cfg.StorageDir = "./var/bug-fab"

    adapter, err := bugfab.New(cfg)
    if err != nil {
        log.Fatal(err)
    }

    r := gin.Default()
    adapter.Register(r.Group("/api/bug-fab"))
    r.Run(":8080")
}
```

That mounts the eight endpoints under `/api/bug-fab`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/bug-fab/bug-reports` | Submit a report (intake) |
| GET | `/api/bug-fab/reports` | List reports with filters |
| GET | `/api/bug-fab/reports/:id` | Fetch one report's full detail |
| GET | `/api/bug-fab/reports/:id/screenshot` | Raw PNG bytes |
| PUT | `/api/bug-fab/reports/:id/status` | Update status + lifecycle entry |
| DELETE | `/api/bug-fab/reports/:id` | Hard-delete |
| POST | `/api/bug-fab/bulk-close-fixed` | Bulk transition fixed -> closed |
| POST | `/api/bug-fab/bulk-archive-closed` | Bulk archive closed reports |

See [`examples/minimal/main.go`](./examples/minimal/main.go) for a runnable consumer.

---

## Auth

This adapter ships no auth. Bug-Fab v0.1 delegates auth entirely to the mount point — attach your existing middleware to the group:

```go
api := r.Group("/api/bug-fab")
api.Use(yourAuthMiddleware())
adapter.Register(api)
```

For asymmetric auth (open intake, admin-only viewer), call `Register` twice with two storage instances pointing at the same dir, or mount two groups with different middleware and selectively skip endpoints. The proper `AuthAdapter` arrives in protocol v0.2.

If your auth middleware identifies the user, stash the identifier so lifecycle entries get the correct `by` field:

```go
api.Use(func(c *gin.Context) {
    c.Set("bug_fab_actor", currentUserFromSession(c))
})
```

---

## Configuration

`Config` is constructed directly or via `NewConfigFromEnv()`. The env vars match the Python reference's names so multi-language deployments share one config block:

| Env var | Default | Purpose |
|---------|---------|---------|
| `BUG_FAB_STORAGE_DIR` | `./var/bug-fab` | Where FileStorage writes report files and index.json. |
| `BUG_FAB_ID_PREFIX` | (empty) | One-letter env tag baked into ids (e.g. `P` -> `bug-P001`). |
| `BUG_FAB_MAX_UPLOAD_MB` | `4` | Screenshot upload cap. Protocol allows up to 10 MiB. |
| `BUG_FAB_RATE_LIMIT_ENABLED` | `false` | Toggle the per-IP fixed-window limiter. |
| `BUG_FAB_RATE_LIMIT_MAX` | `30` | Events per window before 429. |
| `BUG_FAB_RATE_LIMIT_WINDOW_SECONDS` | `60` | Window length in seconds. |
| `BUG_FAB_VIEWER_CAN_EDIT_STATUS` | `true` | Allow `PUT /reports/{id}/status`; `false` returns 403. |
| `BUG_FAB_VIEWER_CAN_DELETE` | `true` | Allow `DELETE /reports/{id}`; `false` returns 403. |
| `BUG_FAB_VIEWER_CAN_BULK` | `true` | Allow the bulk-close / bulk-archive endpoints; `false` returns 403. |

Booleans accept `1`, `true`, `yes` (case-insensitive); anything else is false. The `BUG_FAB_VIEWER_CAN_*` permissions are the exception: they default to `true` (all actions allowed) and accept `0`, `false`, `no` to turn a single destructive action off — the server then rejects that route with `403 forbidden` regardless of the caller.

---

## Storage

The default `FileStorage` writes JSON-per-report plus an `index.json` denormalized listing:

```
<storage_dir>/
├── index.json              denormalized listing for fast filter/page
├── bug-001.json            full report payload
├── bug-001.png             screenshot
└── archive/                bulk-archive destination
    ├── bug-002.json
    └── bug-002.png
```

Atomicity uses tmp+rename for both the index and per-report JSON. Concurrency is process-local — a `sync.Mutex` serializes index reads and writes. Multi-process deployments need either a single worker or an external lock; same caveat as the Python reference.

You can plug in your own storage by implementing the `Storage` interface and constructing `Adapter` directly instead of via `New()`.

---

## Validation rules

Validation runs in this exact order during `POST /bug-reports`:

1. **Rate-limit** (if enabled) -> 429
2. **Metadata JSON parse** -> 400 `validation_error`
3. **Metadata schema validate** -> 422 `schema_error` (per-field FastAPI/Pydantic-shape `loc/msg/type` list)
4. **Unsupported protocol_version** -> 400 `unsupported_protocol_version`
5. **Screenshot size** -> 413 `payload_too_large` with `limit_bytes`
6. **PNG magic bytes** -> 415 `unsupported_media_type`
7. **Save through Storage** -> 201 `Created`

The most common implementation error per upstream PROTOCOL.md notes is silently rewriting unknown enum values (especially `severity`) to defaults — this adapter rejects unknown severities with 422 and includes the offending value in the message. The deprecated-values rule is honored on read: `BugReportDetail` can deserialize any historical status value.

---

## Error envelope

All non-2xx responses (except 204 and the binary 404 from `/screenshot`) use the same JSON envelope:

```json
{
  "error": "validation_error",
  "detail": "metadata.severity must be one of: low, medium, high, critical"
}
```

For 413, `limit_bytes` is added. For 429, `retry_after_seconds` is added. For 422 schema errors, `detail` is a structured array of `{loc, msg, type}` entries (one per failed field).

---

## Conformance status

| Test | Status |
|------|--------|
| Severity 422 on `urgent` | passing (unit) |
| Status 422 on `resolved` | passing (unit) |
| PNG magic-byte 415 | passing (unit) |
| Oversized 413 with `limit_bytes` | passing (unit) |
| Missing `protocol_version` 400 | passing (unit) |
| Unsupported `protocol_version` 400 | passing (unit) |
| List + filter + paginate | passing (unit) |
| Status update appends lifecycle | passing (unit) |
| Delete is 204 with empty body | passing (unit) |
| Bulk-close-fixed transitions only `fixed` | passing (unit) |
| Bulk-archive-closed moves only `closed` | passing (unit) |
| Round-trip file layout vs. Python reference | passing (unit) |
| Rate-limit gates intake at threshold | passing (unit) |
| Conformance pytest suite (upstream) | **passing 30/30** as of 2026-05-21 — `./conformance/run-conformance.sh` boots `examples/minimal/main.go` in a `golang:1.22` container and runs `pytest --bug-fab-conformance` from a sibling `python:3.12` container. |

---

## Running tests

```sh
go test ./...
```

All Go tests are pure-Go and use `t.TempDir()` for storage isolation; no Docker, no fixtures, no live network.

To also verify against the upstream Python conformance suite (boots the example server in Docker and runs `pytest --bug-fab-conformance` against it):

```sh
./conformance/run-conformance.sh
```

See [`conformance/README.md`](./conformance/README.md) for details.
