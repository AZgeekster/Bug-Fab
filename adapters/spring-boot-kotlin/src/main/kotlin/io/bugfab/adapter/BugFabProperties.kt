package io.bugfab.adapter

import org.springframework.boot.context.properties.ConfigurationProperties

/**
 * Type-safe configuration for the Bug-Fab adapter.
 *
 * Bound from Spring's `Environment` under the `bugfab.*` prefix. Spring
 * Boot's relaxed binding means every property is settable via the
 * conventional `BUG_FAB_*` environment variables consumers of the
 * Python reference are used to:
 *
 *   bugfab.route-prefix           BUG_FAB_ROUTE_PREFIX=/bug-fab
 *   bugfab.storage                BUG_FAB_STORAGE=file (or "jpa")
 *   bugfab.storage-dir            BUG_FAB_STORAGE_DIR=./var/bug-fab
 *   bugfab.max-screenshot-mb      BUG_FAB_MAX_SCREENSHOT_MB=4
 *   bugfab.rate-limit.enabled     BUG_FAB_RATE_LIMIT_ENABLED=true
 *   bugfab.rate-limit.max-per-window
 *   bugfab.rate-limit.window-seconds
 *   bugfab.viewer-permissions.can-edit-status   etc.
 *   bugfab.id-prefix              BUG_FAB_ID_PREFIX=P
 *   bugfab.github.enabled         BUG_FAB_GITHUB_ENABLED=true
 *
 * Note: Spring Boot binds `bugfab.max-screenshot-mb` to `maxScreenshotMb`
 * via relaxed naming; the env var `BUG_FAB_MAX_SCREENSHOT_MB` reaches
 * the same field because Spring lowercases and dot-normalizes underscores.
 */
@ConfigurationProperties(prefix = "bugfab")
data class BugFabProperties(
    /** URL prefix every Bug-Fab route mounts under. */
    val routePrefix: String = "/bug-fab",

    /** Storage backend. `file` (default) or `jpa`. */
    val storage: String = "file",

    /** Filesystem directory for the file backend's report tree. */
    val storageDir: String = "./var/bug-fab",

    /**
     * Screenshot size cap in MiB. Defaults to 4 MiB (lower than the
     * protocol's 10 MiB ceiling — the spec says adapters MAY enforce
     * smaller caps, and 4 MiB is enough for high-DPI captures while
     * keeping memory pressure predictable for a JVM that may be
     * running on a small heap).
     */
    val maxScreenshotMb: Int = 4,

    /** Metadata JSON cap in KiB. */
    val maxMetadataKb: Int = 256,

    /** Optional `bug-{prefix}NNN` suffix for multi-environment collectors. */
    val idPrefix: String = "",

    /** Rate-limit knobs (disabled by default). */
    val rateLimit: RateLimitProperties = RateLimitProperties(),

    /** Viewer permission flags — per-route mounting gates. */
    val viewerPermissions: ViewerPermissionsProperties = ViewerPermissionsProperties(),

    /** Optional GitHub Issues sync. */
    val github: GitHubProperties = GitHubProperties(),
)

data class RateLimitProperties(
    val enabled: Boolean = false,
    val maxPerWindow: Int = 30,
    val windowSeconds: Int = 60,
    /**
     * Direct-peer addresses allowed to supply `X-Forwarded-For` as the
     * rate-limit key. Empty (the secure default) ignores the header and
     * meters by the direct peer; `"*"` trusts every peer. Mirrors the
     * Python reference's `rate_limit_trusted_proxies`.
     */
    val trustedProxies: Set<String> = emptySet(),
)

data class ViewerPermissionsProperties(
    val canEditStatus: Boolean = true,
    val canDelete: Boolean = true,
    val canBulk: Boolean = true,
)

data class GitHubProperties(
    val enabled: Boolean = false,
    /** "owner/name" form, e.g. "AZgeekster/Bug-Fab". */
    val repository: String = "",
    val personalAccessToken: String = "",
    val apiBase: String = "https://api.github.com",
)
