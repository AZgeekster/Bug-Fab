package io.bugfab.adapter

import io.github.bucket4j.Bandwidth
import io.github.bucket4j.Bucket
import jakarta.servlet.http.HttpServletRequest
import java.time.Duration
import java.util.concurrent.ConcurrentHashMap

/**
 * Per-IP fixed-window rate limiter for the intake endpoint.
 *
 * Backed by Bucket4j with one bucket per source IP, kept in a
 * [ConcurrentHashMap]. The map grows unbounded in theory, but in
 * practice the natural pruning is "process restart"; for very long-
 * lived deployments behind a busy public endpoint, consumers can wrap
 * with Caffeine via Bucket4j's `BucketProxyManager` — see
 * `MIGRATION_NOTES.md` § "Rate limit at scale".
 *
 * The intake controller calls [check] before any expensive work
 * (multipart parsing, magic-byte sniffing) so a flood of requests
 * cannot pump memory just to be rejected.
 */
class BugFabRateLimiter(
    private val maxPerWindow: Int,
    private val windowSeconds: Long,
) {
    private val buckets = ConcurrentHashMap<String, Bucket>()

    /** Returns `true` if the request is allowed, `false` if it should be 429'd. */
    fun check(clientIp: String): Boolean {
        val bucket = buckets.computeIfAbsent(clientIp) { build() }
        return bucket.tryConsume(1)
    }

    fun retryAfterSeconds(): Long = windowSeconds

    private fun build(): Bucket = Bucket.builder()
        .addLimit(
            Bandwidth.builder()
                .capacity(maxPerWindow.toLong())
                .refillIntervally(maxPerWindow.toLong(), Duration.ofSeconds(windowSeconds))
                .build()
        )
        .build()
}

/**
 * Best-effort source-IP extraction. Honors the first hop of
 * `X-Forwarded-For` so deployments behind a reverse proxy meter the
 * actual client. Falls back to the direct peer address; returns
 * `"unknown"` if nothing is available so the limiter still has a
 * stable partition key.
 */
fun resolveClientIp(request: HttpServletRequest): String {
    val forwarded = request.getHeader("X-Forwarded-For")
    if (!forwarded.isNullOrBlank()) {
        val firstHop = forwarded.split(",").firstOrNull()?.trim()
        if (!firstHop.isNullOrBlank()) return firstHop
    }
    return request.remoteAddr ?: "unknown"
}
