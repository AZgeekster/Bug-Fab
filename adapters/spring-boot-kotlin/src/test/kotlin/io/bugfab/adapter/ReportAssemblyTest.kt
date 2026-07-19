package io.bugfab.adapter

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

/**
 * Golden tests for the shared [buildReport]. Both [FileStorage] and
 * [JpaStorage] delegate to it, so pinning its exact output here guards
 * the persisted wire shape against drift — in particular the top-level
 * `status` key and the `created` lifecycle `by` attribution, which the
 * two backends used to duplicate independently.
 */
class ReportAssemblyTest {

    private val now = "2026-01-01T00:00:00Z"

    @Test
    fun `build_report maps a full payload to the fixed wire shape`() {
        val metadata = mapOf(
            "protocol_version" to "0.1",
            "title" to "Checkout crashes",
            "client_ts" to "2026-01-01T00:00:00Z",
            "report_type" to "bug",
            "description" to "It broke",
            "expected_behavior" to "It works",
            "severity" to "high",
            "tags" to listOf("regression", "checkout"),
            "reporter" to mapOf("name" to "Ada", "email" to "ada@example.com", "user_id" to "u-1"),
            "context" to mapOf(
                "module" to "ctx-mod",
                "environment" to "ctx-env",
                "user_agent" to "client-ua",
                "extra" to "kept",
            ),
            "module" to "checkout",
            "environment" to "production",
            "server_user_agent" to "server-ua",
            "submitted_by" to "intake-user",
        )
        val expected = mapOf<String, Any?>(
            "id" to "bug-001",
            "protocol_version" to "0.1",
            "title" to "Checkout crashes",
            "client_ts" to "2026-01-01T00:00:00Z",
            "report_type" to "bug",
            "description" to "It broke",
            "expected_behavior" to "It works",
            "severity" to "high",
            "status" to "open",
            "tags" to listOf("regression", "checkout"),
            "reporter" to mapOf("name" to "Ada", "email" to "ada@example.com", "user_id" to "u-1"),
            "context" to mapOf(
                "module" to "ctx-mod",
                "environment" to "ctx-env",
                "user_agent" to "client-ua",
                "extra" to "kept",
            ),
            "module" to "checkout",
            "created_at" to now,
            "updated_at" to now,
            "has_screenshot" to true,
            "server_user_agent" to "server-ua",
            "client_reported_user_agent" to "client-ua",
            "environment" to "production",
            "github_issue_url" to null,
            "github_issue_number" to null,
            "lifecycle" to listOf(
                mapOf(
                    "action" to "created",
                    "by" to "intake-user",
                    "at" to now,
                    "fix_commit" to "",
                    "fix_description" to "",
                )
            ),
        )
        assertEquals(expected, buildReport("bug-001", metadata, now))
    }

    @Test
    fun `build_report applies defaults and context fallbacks`() {
        val report = buildReport("bug-042", mapOf("context" to mapOf("module" to "from-ctx", "environment" to "from-ctx-env")), now)
        assertEquals("0.1", report["protocol_version"])
        assertEquals("bug", report["report_type"])
        assertEquals("medium", report["severity"])
        assertEquals("open", report["status"])
        assertEquals("from-ctx", report["module"])
        assertEquals("from-ctx-env", report["environment"])
        @Suppress("UNCHECKED_CAST")
        val lifecycle = report["lifecycle"] as List<Map<String, Any?>>
        assertEquals("created", lifecycle[0]["action"])
        assertEquals("", lifecycle[0]["by"])
        assertEquals(now, lifecycle[0]["at"])
    }
}
