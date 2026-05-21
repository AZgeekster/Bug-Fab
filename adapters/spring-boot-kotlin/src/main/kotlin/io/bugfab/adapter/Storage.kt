package io.bugfab.adapter

/**
 * Storage backend abstraction.
 *
 * Two implementations ship with the adapter:
 *
 *  * [FileStorage] — same on-disk layout as the Python reference
 *    (`index.json` + per-report `bug-NNN.json` + `bug-NNN.png`).
 *  * [JpaStorage] — Spring Data JPA backed; H2 for tests, profile-
 *    switchable to Postgres / MySQL.
 *
 * The interface is intentionally synchronous. Spring MVC controllers
 * are blocking by default; consumers who want reactive can wrap with
 * `Mono.fromCallable { storage.saveReport(...) }` without changing
 * the contract.
 *
 * Conformance: every method that takes a report id MUST validate the
 * `bug-[A-Za-z]?\d{3,}` shape before reaching the persistence layer
 * (path-traversal guard). The controller does this once at the top of
 * each handler so backend implementations can assume the id is safe.
 */
interface Storage {
    fun saveReport(metadata: Map<String, Any?>, screenshotBytes: ByteArray): String

    fun getReport(reportId: String): BugReportDetail?

    fun listReports(
        filters: Map<String, String>,
        page: Int,
        pageSize: Int,
    ): Pair<List<BugReportSummary>, Int>

    fun getScreenshotBytes(reportId: String): ByteArray?

    fun updateStatus(
        reportId: String,
        status: String,
        fixCommit: String = "",
        fixDescription: String = "",
        by: String = "",
    ): BugReportDetail?

    fun deleteReport(reportId: String): Boolean

    fun bulkCloseFixed(by: String = ""): Int

    fun bulkArchiveClosed(): Int

    fun setGithubLink(reportId: String, issueNumber: Long, issueUrl: String): BugReportDetail?

    /** Status-count aggregate for the four lifecycle states. */
    fun computeStats(): Map<String, Int>
}
