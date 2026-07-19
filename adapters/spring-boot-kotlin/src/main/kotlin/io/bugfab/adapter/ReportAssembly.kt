package io.bugfab.adapter

import java.time.OffsetDateTime
import java.time.ZoneOffset

/**
 * Shared wire-report assembly for the storage backends.
 *
 * [FileStorage] and [JpaStorage] persist the identical `BugReportDetail`
 * JSON shape. These package-level functions are the single source of
 * truth so the two backends cannot drift — they previously carried
 * byte-for-byte copies of `buildReport` and `nowIso`, which is exactly
 * how the `created` lifecycle `by` attribution and the top-level
 * `status` key drift across adapters.
 */

/**
 * UTC now in the Python reference adapter's ISO-8601 shape so timestamps
 * are identical across language adapters sharing a data directory.
 */
internal fun nowIso(): String = OffsetDateTime.now(ZoneOffset.UTC).toString()

/**
 * Assemble the persisted `BugReportDetail` JSON from validated intake
 * [metadata]. The single source of truth for the on-the-wire report
 * shape every backend writes; [now] is the shared created/updated
 * timestamp so both the top-level fields and the `created` lifecycle
 * event agree.
 */
internal fun buildReport(
    reportId: String,
    metadata: Map<String, Any?>,
    now: String,
): MutableMap<String, Any?> {
    @Suppress("UNCHECKED_CAST")
    val context = (metadata["context"] as? Map<String, Any?>)?.toMutableMap()
        ?: mutableMapOf()
    @Suppress("UNCHECKED_CAST")
    val reporter = (metadata["reporter"] as? Map<String, Any?>) ?: emptyMap()
    return mutableMapOf(
        "id" to reportId,
        "protocol_version" to (metadata["protocol_version"] ?: "0.1"),
        "title" to (metadata["title"] ?: ""),
        "client_ts" to (metadata["client_ts"] ?: ""),
        "report_type" to (metadata["report_type"] ?: "bug"),
        "description" to (metadata["description"] ?: ""),
        "expected_behavior" to (metadata["expected_behavior"] ?: ""),
        "severity" to (metadata["severity"] ?: "medium"),
        "status" to "open",
        "tags" to ((metadata["tags"] as? List<*>) ?: emptyList<String>()),
        "reporter" to mapOf(
            "name" to (reporter["name"] ?: ""),
            "email" to (reporter["email"] ?: ""),
            "user_id" to (reporter["user_id"] ?: ""),
        ),
        "context" to context,
        "module" to (metadata["module"] ?: context["module"] ?: ""),
        "created_at" to now,
        "updated_at" to now,
        "has_screenshot" to true,
        "server_user_agent" to (metadata["server_user_agent"] ?: ""),
        "client_reported_user_agent" to (context["user_agent"] ?: ""),
        "environment" to (metadata["environment"] ?: context["environment"] ?: ""),
        "github_issue_url" to null,
        "github_issue_number" to null,
        "lifecycle" to mutableListOf(
            mapOf(
                "action" to "created",
                "by" to (metadata["submitted_by"] ?: ""),
                "at" to now,
                "fix_commit" to "",
                "fix_description" to "",
            )
        ),
    )
}
