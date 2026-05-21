package io.bugfab.adapter

import com.fasterxml.jackson.annotation.JsonAnyGetter
import com.fasterxml.jackson.annotation.JsonAnySetter
import com.fasterxml.jackson.annotation.JsonIgnoreProperties
import com.fasterxml.jackson.annotation.JsonInclude
import com.fasterxml.jackson.annotation.JsonProperty
import jakarta.validation.Valid
import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.Size

/**
 * Bug-Fab v0.1 wire-protocol schemas — Kotlin data classes mirroring
 * `bug_fab/schemas.py` from the Python reference.
 *
 * Conformance notes (see `repo/docs/PROTOCOL.md`):
 *
 *  * Severity is a closed enum: `low | medium | high | critical`.
 *    Adapters MUST reject unknown values with `422`. Jackson is
 *    configured to throw on unknown enum values, and `@Valid` on the
 *    intake request triggers a `MethodArgumentNotValidException` that
 *    `BugFabExceptionHandler` maps to `422 schema_error`.
 *
 *  * Status is a closed enum on write paths but lenient on read — the
 *    detail schema treats it as a plain string so historical reports
 *    with deprecated values still deserialize (this is the
 *    "deprecated-values rule" from PROTOCOL.md).
 *
 *  * `BugReportContext` is `@JsonAnySetter`-enabled so consumer-specific
 *    diagnostic fields round-trip verbatim.
 *
 *  * `BugReportIntakeResponse` is intentionally minimal — only the four
 *    fields from the protocol's `201 Created` envelope are emitted, NOT
 *    the full detail. Consumers wanting the stored shape do `GET
 *    /reports/{id}` after intake.
 */

/** Closed enum on writes. Mirrors `Severity` in the Python schemas. */
enum class Severity(@JsonProperty val wire: String) {
    LOW("low"),
    MEDIUM("medium"),
    HIGH("high"),
    CRITICAL("critical");

    companion object {
        @com.fasterxml.jackson.annotation.JsonCreator
        @JvmStatic
        fun fromWire(value: String?): Severity {
            // Match the Python adapter's "silent coercion fails" contract:
            // anything outside the four values throws so MVC turns it
            // into a 422.
            return entries.firstOrNull { it.wire == value }
                ?: throw IllegalArgumentException(
                    "severity must be one of: low, medium, high, critical (got '$value')"
                )
        }
    }

    @com.fasterxml.jackson.annotation.JsonValue
    fun toWire(): String = wire
}

/** Closed enum on writes. Lenient on reads — see `Status.fromWireLenient`. */
enum class Status(val wire: String) {
    OPEN("open"),
    INVESTIGATING("investigating"),
    FIXED("fixed"),
    CLOSED("closed");

    companion object {
        @com.fasterxml.jackson.annotation.JsonCreator
        @JvmStatic
        fun fromWire(value: String?): Status {
            return entries.firstOrNull { it.wire == value }
                ?: throw IllegalArgumentException(
                    "status must be one of: open, investigating, fixed, closed (got '$value')"
                )
        }
    }

    @com.fasterxml.jackson.annotation.JsonValue
    fun toWire(): String = wire
}

/** Submitter identity. All fields are opaque strings, each capped at 256 chars. */
@JsonIgnoreProperties(ignoreUnknown = true)
data class Reporter(
    @field:Size(max = 256) val name: String = "",
    @field:Size(max = 256) val email: String = "",
    @field:Size(max = 256, message = "user_id must be ≤256 characters")
    @JsonProperty("user_id")
    val userId: String = "",
)

/**
 * Auto-captured browser context.
 *
 * Uses `@JsonAnySetter` / `@JsonAnyGetter` so consumer-specific extra
 * keys round-trip without protocol changes — matches the Python
 * reference's `extra="allow"` Pydantic config.
 */
data class BugReportContext(
    @JsonProperty("url") val url: String = "",
    @JsonProperty("module") val module: String = "",
    @JsonProperty("user_agent") val userAgent: String = "",
    @JsonProperty("viewport_width") val viewportWidth: Int = 0,
    @JsonProperty("viewport_height") val viewportHeight: Int = 0,
    @JsonProperty("console_errors") val consoleErrors: List<Map<String, Any?>> = emptyList(),
    @JsonProperty("network_log") val networkLog: List<Map<String, Any?>> = emptyList(),
    @JsonProperty("source_mapping") val sourceMapping: Map<String, Any?> = emptyMap(),
    @JsonProperty("app_version") val appVersion: String = "",
    @JsonProperty("environment") val environment: String = "",
) {
    private val _extras = mutableMapOf<String, Any?>()

    @JsonAnyGetter
    fun extras(): Map<String, Any?> = _extras

    @JsonAnySetter
    fun setExtra(key: String, value: Any?) {
        _extras[key] = value
    }
}

/** Inbound submission payload — the JSON body of the `metadata` form part. */
@JsonIgnoreProperties(ignoreUnknown = true)
data class BugReportCreate(
    @JsonProperty("protocol_version") val protocolVersion: String,
    @field:NotBlank @field:Size(min = 1, max = 200) val title: String,
    @field:NotBlank @JsonProperty("client_ts") val clientTs: String,
    @JsonProperty("report_type") val reportType: String = "bug",
    val description: String = "",
    @JsonProperty("expected_behavior") val expectedBehavior: String = "",
    val severity: Severity = Severity.MEDIUM,
    val tags: List<String> = emptyList(),
    @field:Valid val reporter: Reporter = Reporter(),
    @field:Valid val context: BugReportContext = BugReportContext(),
)

/** `PUT /reports/{id}/status` body. */
data class BugReportStatusUpdate(
    val status: Status,
    @JsonProperty("fix_commit") val fixCommit: String = "",
    @JsonProperty("fix_description") val fixDescription: String = "",
)

/** Lifecycle audit entry. Append-only. */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class LifecycleEvent(
    val action: String,
    val by: String = "",
    val at: String,
    @JsonProperty("fix_commit") val fixCommit: String = "",
    @JsonProperty("fix_description") val fixDescription: String = "",
    val status: String? = null,
)

/**
 * Compact list-item shape. Severity/status are emitted as raw strings
 * because the read paths MUST tolerate deprecated values (per the
 * protocol's deprecated-values rule).
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class BugReportSummary(
    val id: String,
    val title: String,
    @JsonProperty("report_type") val reportType: String = "bug",
    val severity: String = "medium",
    val status: String = "open",
    val module: String = "",
    @JsonProperty("created_at") val createdAt: String,
    @JsonProperty("has_screenshot") val hasScreenshot: Boolean = true,
    @JsonProperty("github_issue_url") val githubIssueUrl: String? = null,
)

/** Full detail payload. Extends the summary shape with everything the viewer needs. */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class BugReportDetail(
    val id: String,
    val title: String,
    @JsonProperty("report_type") val reportType: String = "bug",
    val severity: String = "medium",
    val status: String = "open",
    val module: String = "",
    @JsonProperty("created_at") val createdAt: String,
    @JsonProperty("has_screenshot") val hasScreenshot: Boolean = true,
    @JsonProperty("github_issue_url") val githubIssueUrl: String? = null,
    val description: String = "",
    @JsonProperty("expected_behavior") val expectedBehavior: String = "",
    val tags: List<String> = emptyList(),
    val reporter: Reporter = Reporter(),
    val context: BugReportContext = BugReportContext(),
    val lifecycle: List<LifecycleEvent> = emptyList(),
    @JsonProperty("server_user_agent") val serverUserAgent: String = "",
    @JsonProperty("client_reported_user_agent") val clientReportedUserAgent: String = "",
    val environment: String = "",
    @JsonProperty("client_ts") val clientTs: String = "",
    @JsonProperty("protocol_version") val protocolVersion: String = "0.1",
    @JsonProperty("updated_at") val updatedAt: String = "",
    @JsonProperty("github_issue_number") val githubIssueNumber: Long? = null,
)

/** Pagination envelope for `GET /reports`. */
data class BugReportListResponse(
    val items: List<BugReportSummary>,
    val total: Int,
    val page: Int = 1,
    @JsonProperty("page_size") val pageSize: Int = 20,
    val stats: Map<String, Int> = emptyMap(),
)

/** The four-field `201 Created` envelope for `POST /bug-reports`. */
data class BugReportIntakeResponse(
    val id: String,
    @JsonProperty("received_at") val receivedAt: String,
    @JsonProperty("stored_at") val storedAt: String,
    @JsonProperty("github_issue_url") val githubIssueUrl: String? = null,
)

/** Wire-protocol error envelope. */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class ErrorEnvelope(
    val error: String,
    val detail: Any,
    @JsonProperty("retry_after_seconds") val retryAfterSeconds: Int? = null,
    @JsonProperty("limit_bytes") val limitBytes: Long? = null,
)
