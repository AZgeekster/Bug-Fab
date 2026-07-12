package io.bugfab.adapter

import io.github.bucket4j.Bandwidth
import io.github.bucket4j.Bucket
import jakarta.servlet.http.HttpServletRequest
import java.time.Duration
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/**
 * Per-IP fixed-window rate limiter for the intake endpoint.
 *
 * Backed by Bucket4j with one bucket per source IP, kept in a
 * [ConcurrentHashMap]. Idle buckets are evicted by a sweep that runs at
 * most once per window: without it, a client cycling through source keys
 * (the trivial outcome of a spoofed `X-Forwarded-For`) grows the map
 * without bound, so *enabling* the limiter would itself be a
 * memory-exhaustion sink. An idle bucket has fully refilled by the time
 * it is evicted, so recreating it on the next request is
 * behavior-neutral.
 *
 * The intake controller calls [check] before any expensive work
 * (multipart parsing, magic-byte sniffing) so a flood of requests
 * cannot pump memory just to be rejected.
 */
class BugFabRateLimiter(
    private val maxPerWindow: Int,
    private val windowSeconds: Long,
) {
    private class Entry(val bucket: Bucket) {
        @Volatile var lastAccessMillis: Long = System.currentTimeMillis()
    }

    private val buckets = ConcurrentHashMap<String, Entry>()
    private val lastSweepMillis = AtomicLong(System.currentTimeMillis())

    /** Returns `true` if the request is allowed, `false` if it should be 429'd. */
    fun check(clientIp: String): Boolean {
        val now = System.currentTimeMillis()
        sweep(now)
        val entry = buckets.computeIfAbsent(clientIp) { Entry(build()) }
        entry.lastAccessMillis = now
        return entry.bucket.tryConsume(1)
    }

    fun retryAfterSeconds(): Long = windowSeconds

    /** Number of tracked source keys. Exposed for tests. */
    internal fun trackedKeys(): Int = buckets.size

    /**
     * Evict buckets idle for a full window. Throttled to once per window
     * so the full scan is amortized O(1) across [check] calls; the CAS
     * guard keeps concurrent callers from sweeping twice.
     */
    private fun sweep(now: Long) {
        val windowMillis = windowSeconds * 1000
        val last = lastSweepMillis.get()
        if (now - last < windowMillis) return
        if (!lastSweepMillis.compareAndSet(last, now)) return
        buckets.entries.removeIf { now - it.value.lastAccessMillis >= windowMillis }
    }

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
 * Source-IP extraction for the rate-limit key.
 *
 * `X-Forwarded-For` is client-controlled and trivially spoofed: rotating
 * it per request mints a fresh bucket each time and defeats the limiter
 * entirely. The header is therefore honored **only** when the direct
 * peer is listed in [trustedProxies] (or the set contains the wildcard
 * `"*"`, restoring the old always-trust behavior for deployments that
 * terminate every request behind a proxy they control). Only the first
 * hop is read. Returns `"unknown"` if nothing is available so the
 * limiter still has a stable partition key.
 */
fun resolveClientIp(request: HttpServletRequest, trustedProxies: Set<String>): String {
    val peer: String? = request.remoteAddr
    val forwarded = request.getHeader("X-Forwarded-For")
    val peerTrusted = (peer != null && peer in trustedProxies) || "*" in trustedProxies
    if (!forwarded.isNullOrBlank() && peerTrusted) {
        val firstHop = forwarded.split(",").firstOrNull()?.trim()
        if (!firstHop.isNullOrBlank()) return firstHop
    }
    return peer ?: "unknown"
}
