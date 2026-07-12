# Bug-Fab Spring Boot adapter (Kotlin)

A Spring Boot 3 / Kotlin adapter for the [Bug-Fab wire protocol](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md). Implements every v0.1 endpoint, with two interchangeable storage backends (file and JPA), Bean Validation on writes, Bucket4j-backed rate limiting, and a strict PNG magic-byte check on the intake path.

> Status: first-party reference adapter for the JVM / Kotlin ecosystem. Promoted from draft on 2026-05-21 after `gradle build test` was verified at 24/24 passing under `gradle:8-jdk17`. Not yet published to Maven Central (in-repo source; install via Gradle source dep or local build). Tracked in the Bug-Fab adapters registry: <https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#jvm-spring-boot-kotlin>.

## Install

When the package is published, consumers will pull it in via:

```kotlin
dependencies {
    implementation("io.bugfab:bugfab-spring:0.1.0-SNAPSHOT")
}
```

Until then, copy this directory into your repository and add it as an included build:

```kotlin
// settings.gradle.kts
includeBuild("path/to/bugfab-spring")
```

```kotlin
// build.gradle.kts
dependencies {
    implementation("io.bugfab:bugfab-spring")
}
```

## 10-line wiring

```kotlin
// MyApplication.kt
@SpringBootApplication
class MyApplication

fun main(args: Array<String>) {
    runApplication<MyApplication>(*args)
}
```

```yaml
# application.yml
bugfab:
  storage: file
  storage-dir: ./var/bug-fab
  route-prefix: /bug-fab
```

That's it. Auto-configuration mounts the eight endpoints under `/bug-fab`:

| Method | Path                                       | Purpose          |
|--------|--------------------------------------------|------------------|
| POST   | /bug-fab/bug-reports                       | Submit a report  |
| GET    | /bug-fab/reports                           | List reports     |
| GET    | /bug-fab/reports/{id}                      | Fetch one        |
| GET    | /bug-fab/reports/{id}/screenshot           | Raw PNG bytes    |
| PUT    | /bug-fab/reports/{id}/status               | Update status    |
| DELETE | /bug-fab/reports/{id}                      | Delete           |
| POST   | /bug-fab/bulk-close-fixed                  | Bulk close       |
| POST   | /bug-fab/bulk-archive-closed               | Bulk archive     |

No `@EnableBugFab` annotation is required — the auto-configuration class is registered via `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`.

## Configuration

| Property                                | Default              | Env var                                        | Notes                                                              |
|-----------------------------------------|----------------------|------------------------------------------------|--------------------------------------------------------------------|
| `bugfab.route-prefix`                   | `/bug-fab`           | `BUGFAB_ROUTE_PREFIX`                          | URL prefix for every endpoint.                                     |
| `bugfab.storage`                        | `file`               | `BUGFAB_STORAGE`                               | `file` or `jpa`.                                                   |
| `bugfab.storage-dir`                    | `./var/bug-fab`      | `BUGFAB_STORAGE_DIR`                           | Filesystem dir used by the file backend.                           |
| `bugfab.max-screenshot-mb`              | `4`                  | `BUGFAB_MAX_SCREENSHOT_MB`                     | Hard cap for screenshot bytes.                                     |
| `bugfab.max-metadata-kb`                | `256`                | `BUGFAB_MAX_METADATA_KB`                       | Cap for the metadata part; oversize is `413 payload_too_large`.    |
| `bugfab.id-prefix`                      | (empty)              | `BUGFAB_ID_PREFIX`                             | Multi-env shared collectors (`bug-P038`, `bug-D012`).              |
| `bugfab.rate-limit.enabled`             | `false`              | `BUGFAB_RATE_LIMIT_ENABLED`                    | Bucket4j per-IP limiter on intake.                                 |
| `bugfab.rate-limit.max-per-window`      | `30`                 | `BUGFAB_RATE_LIMIT_MAX_PER_WINDOW`             |                                                                    |
| `bugfab.rate-limit.window-seconds`      | `60`                 | `BUGFAB_RATE_LIMIT_WINDOW_SECONDS`             |                                                                    |
| `bugfab.rate-limit.trusted-proxies`     | (empty)              | `BUGFAB_RATE_LIMIT_TRUSTED_PROXIES`            | Peers allowed to supply `X-Forwarded-For`; `*` trusts all.         |
| `bugfab.viewer-permissions.can-edit-status` | `true`           | `BUGFAB_VIEWER_PERMISSIONS_CAN_EDIT_STATUS`    | Gates `PUT /reports/{id}/status`.                                  |
| `bugfab.viewer-permissions.can-delete`  | `true`               | `BUGFAB_VIEWER_PERMISSIONS_CAN_DELETE`         | Gates `DELETE /reports/{id}`.                                      |
| `bugfab.viewer-permissions.can-bulk`    | `true`               | `BUGFAB_VIEWER_PERMISSIONS_CAN_BULK`           | Gates the two `/bulk-*` endpoints.                                 |
| `bugfab.github.enabled`                 | `false`              | `BUGFAB_GITHUB_ENABLED`                        | (Stub) opt-in for GitHub Issues sync — wiring lands in v0.2.       |

Spring Boot's relaxed binding accepts `bugfab.rate-limit.enabled`, `bugfab.rateLimit.enabled`, and `BUGFAB_RATE_LIMIT_ENABLED` interchangeably.

When `bugfab.storage=jpa`, the consumer MUST also configure a DataSource. Spring Boot's auto-configuration picks up any JDBC driver on the classpath (Postgres, MySQL, H2 for tests). The adapter ships no DDL — it relies on `spring.jpa.hibernate.ddl-auto` (or, recommended for production, a Flyway/Liquibase migration; see `MIGRATION_NOTES.md`).

## Storage backends

### `FileStorage`

Mirrors the on-disk layout of the Python reference: `index.json` + per-report `bug-NNN.json` + `bug-NNN.png`, with atomic tmp+rename writes. Default for single-instance hobby deployments.

### `JpaStorage`

Spring Data JPA against any JDBC backend. One table (`bug_fab_reports`) with the full report stored as JSON plus four denormalized columns for filterable queries. H2 in tests; Postgres / MySQL in production.

## Rate limiting

Disabled by default. Set `bugfab.rate-limit.enabled=true` to wire the per-IP Bucket4j limiter. Rejected requests return the documented `{error: "rate_limited", retry_after_seconds}` envelope plus a `Retry-After` header.

`X-Forwarded-For` is client-controlled and spoofable, so it is honored only when the direct peer is listed in `bugfab.rate-limit.trusted-proxies` (comma-separated; `*` trusts every peer). Empty — the secure default — meters by the direct peer address, so list your reverse-proxy IPs to restore per-end-user metering behind a proxy.

Idle buckets are evicted by a sweep that runs at most once per window, so long-lived deployments no longer grow the bucket map without bound. For distributed rate limiting across instances, swap in Bucket4j's `BucketProxyManager` — see `MIGRATION_NOTES.md` § "Rate limit at scale."

## Auth

Bug-Fab v0.1 ships no auth abstraction. Mount the controller under a prefix that your existing Spring Security `SecurityFilterChain` already protects:

```kotlin
@Bean
fun securityFilterChain(http: HttpSecurity): SecurityFilterChain =
    http.authorizeHttpRequests { auth ->
        auth.requestMatchers("/bug-fab/bug-reports").permitAll()
        auth.requestMatchers("/bug-fab/**").authenticated()
        auth.anyRequest().permitAll()
    }.build()
```

The viewer permissions config (`can-edit-status`, `can-delete`, `can-bulk`) is an in-band veto on top of the mount-point auth — useful for read-only operator roles.

## Conformance status

Cross-stack `bug-fab-conformance` (pytest plugin) run against `examples:minimal`
on 2026-05-21: **7 / 30 passing, 8 failing, 15 skipped (cascade from the 8
failures)**. Driver and gap summary live in [`conformance/`](./conformance/);
re-run with `./conformance/run-conformance.sh` (Docker required).

The skips are cascading — most viewer / status-workflow / response-shape
tests need a successful submission to set up their fixtures, and intake
submissions currently fail with `400` because Spring's multipart binding
expects `metadata` as a typed JSON part (`Content-Type: application/json`)
while the conformance suite posts it as an untyped form field (the
`httpx`/`requests`/`curl --form` shape that the wire protocol leaves
implicit). Tracked separately — once that one binding is loosened, the
cascade clears.

| Conformance area               | Status                                                                 |
|-------------------------------- |------------------------------------------------------------------------|
| Wire protocol v0.1 endpoints    | All 8 implemented                                                      |
| Cross-stack conformance suite   | **7 / 30** — see `conformance/` (intake multipart-binding gap)         |
| Severity enum (strict on write) | Conformant — `urgent` rejected (currently returns 400; suite wants 422)|
| Status enum (lenient on read)   | Conformant — string-typed on detail / summary models                   |
| `protocol_version` rejection    | Conformant — `400 unsupported_protocol_version`                        |
| Screenshot magic-byte check     | Conformant — `415 unsupported_media_type` for non-PNG                  |
| Screenshot size cap             | Conformant — controller-level + `MaxUploadSizeExceededException`       |
| User-Agent trust boundary       | Conformant — `server_user_agent` captured from request header          |
| Lifecycle audit log             | Conformant — `created` on intake, `status_changed` on each update      |
| Path-traversal guard            | Conformant — `bug-[A-Za-z]?\d{3,}` regex at controller + storage layer |
| Rate-limit response shape       | Conformant — `Retry-After` header + envelope                           |

## Building

```bash
./gradlew build test           # build + run all tests
./gradlew :examples:minimal:bootRun    # run the example consumer
```

JDK 17+ required. Kotlin 1.9+. Spring Boot 3.3+.

## CSRF / Antiforgery

The intake endpoint must NOT be CSRF-protected — the JS bundle posts cross-origin from the host page. The viewer's mutating endpoints (status update, delete, bulk) ARE state-changing, but Bug-Fab's v0.1 stance is "protect them via mount-point auth, not CSRF tokens" because the protocol has no auth abstraction yet. A future flag analogous to the ASP.NET adapter's `EnableAntiforgeryOnViewer` may land in v0.2.

## See also

- [`MIGRATION_NOTES.md`](./MIGRATION_NOTES.md) — Spring-specific notes (DI scopes, profile activation, JPA migration generation, gotchas).
- [`examples/minimal/`](./examples/minimal/) — runnable consumer app, 10 lines of wiring.
- [Bug-Fab wire protocol](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md) — authoritative spec this adapter implements.
