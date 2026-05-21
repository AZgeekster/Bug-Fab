# MIGRATION_NOTES — Vapor adapter

Swift-specific notes for anyone porting Bug-Fab functionality into a Vapor app or extending this adapter.

## Toolchain — Swift 6 + Vapor 4.92+

This adapter requires **Swift 6.0+** and **Vapor 4.92+**. It does not build on Swift 5.10 or earlier. Three Swift 6 strict-concurrency adaptations matter for anyone porting code in or out:

1. **`BugReportsController` is `Sendable`.** Vapor's `@Sendable` annotation on `async` route closures is only valid when the enclosing type is itself `Sendable`. Declaring the controller `public struct BugReportsController: RouteCollection, Sendable` is what lets the per-route `@Sendable func submit(req:)` etc. compile under strict concurrency. Reverting to a non-`Sendable` controller is a Swift 6 build error, not a warning.

2. **Async XCTVapor pattern.** Tests use the new `app.testable().test(.POST, path) { req async throws in ... } afterResponse: { res async throws in ... }` form. The synchronous closure form from Vapor 4.50-era docs is gone under Swift 6 + Vapor 4.92 — the test EventLoop is no longer reachable from a sync closure when strict concurrency is on.

3. **`Application` is now `async`-constructed.** Tests use `try await Application.make(.testing)` and `try? await app.asyncShutdown()`. The older `Application(.testing)` initializer still exists but is deprecated.

## Error envelope — replace, do not prepend

Vapor pre-registers `ErrorMiddleware.default(environment:)` on every `Application`. It emits `{"error": true, "reason": "..."}` — **not** the spec's `{"error": "<code>", "detail": "..."}` envelope. Prepending a replacement middleware with `app.middleware.use(..., at: .beginning)` does NOT win: the default middleware sits inside ours and catches the thrown `AbortError` first, returning a `Response` (not re-throwing). Our outer middleware then sees a successful response and the wire shape leaks `error: true` as a boolean.

The fix in `Configure.swift` is to rebuild middleware from scratch:

```swift
self.middleware = .init()
self.middleware.use(BugFabErrorMiddleware())
```

This regression cost ~6 turns to diagnose during the 2026-05-21 fix pass. If you ever copy this adapter as a starting point for a different Vapor service, keep the reset.

## Async/await all the way down

Vapor 4 has fully migrated to `async/await` (Swift Concurrency); the historical `EventLoopFuture<T>` surface is still available but no new code in this adapter uses it. Routes are `@Sendable` async closures; the `BugFabStorage` protocol is `async throws` end to end. If you maintain a Vapor 4.50+ project that still mixes `EventLoopFuture` and `async`, prefer adopting `async` at the boundary — every controller in this adapter is async-first.

Why this matters for Bug-Fab specifically: the older `EventLoopFuture` patterns made it tempting to chain blocking work onto the request's loop, which would have stalled the multipart body decoder for the duration of a slow disk write. The async-first surface lets us push `BugFabFileStorage`'s blocking I/O onto a detached Task (`Task.detached(priority: .userInitiated)` inside `BugFabFileStorage.detachedThrowing`) so the EventLoop stays clean.

## EventLoop vs Task.detached

`BugFabFileStorage` uses `Task.detached` to keep filesystem syscalls off the EventLoop. This is the conservative choice — Fluent's I/O is already non-blocking, but raw `FileManager` / `Data(contentsOf:)` are not. If you swap in a custom storage backend that talks to a non-blocking service (S3 via async-http-client, Redis via RediStack), you can drop the detached hop.

## Codable strictness & deprecated enum values

The protocol's deprecated-values rule (§ "CRITICAL: Adapters MUST accept deprecated enum values on read") is enforced by representing the **read-side** shapes (`BugFabBugReportSummary`, `BugFabBugReportDetail`) as plain `String` fields, *not* as the strict `BugFabSeverity` / `BugFabStatus` enums. The strict enums only appear on the write side (`BugFabBugReportCreate`, `BugFabStatusUpdate`). If you tighten this in a later revision, expect to break consumers who store historical reports.

Practical knock-on: the `BugFabBugReportDetail` you get back from `GET /reports/{id}` is permissive on `severity` and `status`. Don't pattern-match on enum values without a fallback case.

## Fluent property-wrapper traps

`@ID(custom: "id", generatedBy: .user)` — we set the id ourselves (`bug-001`, etc.) rather than letting Fluent generate a UUID. If you migrate to a `UUID` primary key in a future revision, you'll need to backfill the old `bug-NNN` ids into a separate column to keep client links working.

`@Field(key: "screenshot") var screenshot: Data?` stores the PNG inline. That's fine for small / occasional traffic but explodes the row size — for production deployments, swap this for a `screenshot_path` column and route the actual bytes through S3 or a filesystem volume.

## body.collect(maxSize:) is mandatory

Don't call `req.body.collect()` without a size argument anywhere in this adapter. The default cap (`Application.routes.defaultMaxBodySize`) is reset in `Configure.swift` to `maxUploadBytes + 1 MiB` so the multipart decoder won't buffer unbounded uploads. The route-level `body: .collect(maxSize: "12mb")` is a second guardrail.

## macOS vs Linux Process lifetimes in tests

`XCTVapor`'s `testable()` runs the request through the configured router on a test EventLoop with the async test closures described above. On macOS this works in `swift test` against the system Swift 6+ toolchain; on Linux it works on the Swift 6 Docker images (`swift:6.0`, `swift:6.0-jammy`). We have not exercised the `URL(fileURLWithPath:)` tmp dir layout on Windows — Swift on Windows is officially supported as of Swift 5.10, but the file-storage backend hasn't been tested there. Pin Linux-only CI for now.

## Swift Package Index discoverability

When this adapter ships as a standalone package, register it on [Swift Package Index](https://swiftpackageindex.com/) — that's where the Vapor ecosystem looks for packages. The package will need a `.spi.yml` listing supported platforms and a public GitHub release tag.

## What the conformance suite expects vs what this adapter does

The Python conformance suite (`repo/tests/conformance/`) hits the HTTP surface, not the Swift API, so it doesn't care about Swift idioms. Things to verify when you run conformance against this adapter:

1. `severity: "urgent"` → 422 with `error: "schema_error"` (✓ — see `BugFabValidationTests.testSeverityRejected`).
2. Deprecated `status` values must round-trip through reads. **The current adapter does NOT have an explicit deprecated-status test** — there are no deprecated statuses yet in v0.1, but the read-side `String` typing will accept them.
3. The intake response is the minimal envelope (`id`, `received_at`, `stored_at`, `github_issue_url`) — **not** the full `BugReportDetail`. We honor this.
4. `stored_at` is opaque; we return `bug-fab://reports/{id}`. Don't parse it on the client side.

## Not implemented

- GitHub Issues sync — left out of v0.1 to keep scope tight. The protocol contract for sync is best-effort (failure must not yield non-2xx) so plugging it in later is additive.
- Webhook fanout — same reasoning.
- PII redaction — left for the consumer to wire in via a custom storage decorator.
- A vendored frontend bundle — the Python reference ships `static/`; consumers of this Vapor adapter would point their `app.middleware.use(FileMiddleware(...))` at whatever static directory hosts the bundle.

## Verification status

Verified 2026-05-21 under `swift:6.0` (Docker). 9/9 tests pass:

```bash
docker run --rm -v "$(pwd):/draft" -w /draft swift:6.0 \
  sh -c 'swift build && swift test'
```

This is the supported verification path on hosts without a native Swift 6 toolchain (Windows, older macOS). The `swift:6.0` image bundles Vapor 4.92's transitive build deps and resolves `Package.resolved` cleanly.
