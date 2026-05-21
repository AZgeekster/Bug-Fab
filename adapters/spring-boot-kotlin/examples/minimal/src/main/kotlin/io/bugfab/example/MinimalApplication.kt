package io.bugfab.example

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication

/**
 * Minimal Bug-Fab consumer.
 *
 * Wiring is literally this file plus the dependency in `build.gradle.kts`.
 * Auto-configuration handles:
 *   - mounting the eight endpoints under `bugfab.routePrefix`
 *   - selecting the storage backend from `bugfab.storage`
 *   - registering the rate limiter if `bugfab.rateLimit.enabled` is true
 *
 * Run with:  ./gradlew :examples:minimal:bootRun
 * Submit a report:
 *   curl -F 'metadata={"protocol_version":"0.1","title":"hi","client_ts":"2026-04-27T00:00:00Z"};type=application/json' \
 *        -F 'screenshot=@./screenshot.png;type=image/png' \
 *        http://localhost:8080/bug-fab/bug-reports
 */
@SpringBootApplication
class MinimalApplication

fun main(args: Array<String>) {
    runApplication<MinimalApplication>(*args)
}
