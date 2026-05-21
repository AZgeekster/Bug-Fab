package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.concurrent.locks.ReentrantLock
import kotlin.io.path.exists
import kotlin.io.path.notExists

/**
 * File-backed storage backend. Mirrors `bug_fab/storage/files.py`.
 *
 * On-disk layout under [storageDir]:
 *
 *     <storageDir>/
 *      ├── index.json            denormalized listing
 *      ├── bug-001.json          full report payload
 *      ├── bug-001.png           screenshot
 *      └── archive/
 *           ├── bug-002.json
 *           └── bug-002.png
 *
 * Atomicity uses tmp+rename for both `index.json` and per-report JSON
 * so a crash mid-write never publishes a torn file. Concurrency is
 * coordinated by a per-instance [ReentrantLock] — process-local only,
 * same caveat as the Python reference: multi-JVM deployments must use
 * [JpaStorage] or accept races on the index file.
 */
class FileStorage(
    storageDir: Path,
    private val idPrefix: String = "",
) : Storage {

    constructor(storageDir: String, idPrefix: String = "") :
        this(Path.of(storageDir), idPrefix)

    private val storageDir: Path = storageDir.toAbsolutePath()
    private val archiveDir: Path = this.storageDir.resolve("archive")
    private val indexPath: Path = this.storageDir.resolve("index.json")
    private val lock = ReentrantLock()
    private val mapper: ObjectMapper = jacksonObjectMapper()

    init {
        Files.createDirectories(this.storageDir)
        Files.createDirectories(archiveDir)
    }

    override fun saveReport(metadata: Map<String, Any?>, screenshotBytes: ByteArray): String {
        lock.lock()
        try {
            val index = readIndex()
            val reportId = nextId(index)
            val now = nowIso()
            val report = buildReport(reportId, metadata, now)
            writeScreenshot(reportId, screenshotBytes)
            writeReport(reportId, report)
            val entry = buildIndexEntry(report)
            @Suppress("UNCHECKED_CAST")
            val reports = (index["reports"] as MutableList<Map<String, Any?>>)
            reports.add(entry)
            index["next_number"] = (index["next_number"] as Int) + 1
            writeIndex(index)
            return reportId
        } finally {
            lock.unlock()
        }
    }

    override fun getReport(reportId: String): BugReportDetail? {
        if (!isValidReportId(reportId)) return null
        lock.lock()
        try {
            val raw = readReport(reportId) ?: return null
            return coerceDetail(raw)
        } finally {
            lock.unlock()
        }
    }

    override fun listReports(
        filters: Map<String, String>,
        page: Int,
        pageSize: Int,
    ): Pair<List<BugReportSummary>, Int> {
        lock.lock()
        val entries: List<Map<String, Any?>>
        try {
            val index = readIndex()
            @Suppress("UNCHECKED_CAST")
            entries = (index["reports"] as List<Map<String, Any?>>).toList()
        } finally {
            lock.unlock()
        }
        val matched = entries.filter { matchesFilters(it, filters) }
            .sortedByDescending { it["created_at"] as? String ?: "" }
        val total = matched.size
        val start = ((page - 1) * pageSize).coerceAtLeast(0)
        val end = (start + pageSize).coerceAtMost(total)
        val pageItems = if (start >= total) emptyList()
        else matched.subList(start, end).map { coerceSummary(it) }
        return pageItems to total
    }

    override fun getScreenshotBytes(reportId: String): ByteArray? {
        if (!isValidReportId(reportId)) return null
        val primary = storageDir.resolve("$reportId.png")
        if (primary.exists()) return Files.readAllBytes(primary)
        val archived = archiveDir.resolve("$reportId.png")
        if (archived.exists()) return Files.readAllBytes(archived)
        return null
    }

    override fun updateStatus(
        reportId: String,
        status: String,
        fixCommit: String,
        fixDescription: String,
        by: String,
    ): BugReportDetail? {
        if (!isValidReportId(reportId)) return null
        lock.lock()
        try {
            val data = readReport(reportId)?.toMutableMap() ?: return null
            data["status"] = status
            val now = nowIso()
            data["updated_at"] = now
            @Suppress("UNCHECKED_CAST")
            val lifecycle = (data["lifecycle"] as? MutableList<Map<String, Any?>>)
                ?: mutableListOf<Map<String, Any?>>().also { data["lifecycle"] = it }
            lifecycle.add(
                mapOf(
                    "action" to "status_changed",
                    "by" to by,
                    "at" to now,
                    "fix_commit" to fixCommit,
                    "fix_description" to fixDescription,
                    "status" to status,
                )
            )
            writeReport(reportId, data)
            updateIndexEntry(reportId, mapOf("status" to status))
            return coerceDetail(data)
        } finally {
            lock.unlock()
        }
    }

    override fun deleteReport(reportId: String): Boolean {
        if (!isValidReportId(reportId)) return false
        lock.lock()
        try {
            var removed = false
            for (path in candidatePaths(reportId)) {
                if (path.exists()) {
                    Files.deleteIfExists(path)
                    removed = true
                }
            }
            if (removed) {
                val index = readIndex()
                @Suppress("UNCHECKED_CAST")
                val reports = (index["reports"] as MutableList<Map<String, Any?>>)
                reports.removeAll { it["id"] == reportId }
                writeIndex(index)
            }
            return removed
        } finally {
            lock.unlock()
        }
    }

    override fun bulkCloseFixed(by: String): Int {
        val ids: List<String>
        lock.lock()
        try {
            val index = readIndex()
            @Suppress("UNCHECKED_CAST")
            val reports = index["reports"] as List<Map<String, Any?>>
            ids = reports.filter { it["status"] == "fixed" }
                .mapNotNull { it["id"] as? String }
        } finally {
            lock.unlock()
        }
        var closed = 0
        for (id in ids) {
            if (updateStatus(id, status = "closed", by = by) != null) closed++
        }
        return closed
    }

    override fun bulkArchiveClosed(): Int {
        lock.lock()
        try {
            val index = readIndex()
            @Suppress("UNCHECKED_CAST")
            val reports = index["reports"] as List<Map<String, Any?>>
            val ids = reports.filter { it["status"] == "closed" }
                .mapNotNull { it["id"] as? String }
            var archived = 0
            for (id in ids) {
                if (archiveOne(id)) archived++
            }
            return archived
        } finally {
            lock.unlock()
        }
    }

    override fun setGithubLink(
        reportId: String,
        issueNumber: Long,
        issueUrl: String,
    ): BugReportDetail? {
        if (!isValidReportId(reportId)) return null
        lock.lock()
        try {
            val data = readReport(reportId)?.toMutableMap() ?: return null
            data["github_issue_number"] = issueNumber
            data["github_issue_url"] = issueUrl
            writeReport(reportId, data)
            updateIndexEntry(reportId, mapOf("github_issue_url" to issueUrl))
            return coerceDetail(data)
        } finally {
            lock.unlock()
        }
    }

    override fun computeStats(): Map<String, Int> {
        val states = listOf("open", "investigating", "fixed", "closed")
        val (_, _) = listReports(emptyMap(), 1, 1) // touch storage
        val out = mutableMapOf<String, Int>()
        for (state in states) {
            val (_, total) = listReports(mapOf("status" to state), 1, 1)
            out[state] = total
        }
        return out
    }

    // --- helpers ---

    private fun nextId(index: MutableMap<String, Any?>): String {
        val n = (index["next_number"] as? Int) ?: 1
        return "bug-${idPrefix}${"%03d".format(n)}"
    }

    private fun buildReport(
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

    private fun buildIndexEntry(report: Map<String, Any?>): Map<String, Any?> = mapOf(
        "id" to report["id"],
        "title" to (report["title"] ?: ""),
        "report_type" to (report["report_type"] ?: "bug"),
        "severity" to (report["severity"] ?: "medium"),
        "status" to (report["status"] ?: "open"),
        "module" to (report["module"] ?: ""),
        "created_at" to (report["created_at"] ?: ""),
        "has_screenshot" to (report["has_screenshot"] ?: true),
        "github_issue_url" to report["github_issue_url"],
    )

    private fun matchesFilters(entry: Map<String, Any?>, filters: Map<String, String>): Boolean {
        for (key in listOf("status", "severity", "module", "report_type", "environment")) {
            val wanted = filters[key] ?: continue
            if (wanted.isBlank()) continue
            if ((entry[key] as? String) != wanted) return false
        }
        return true
    }

    private fun coerceSummary(entry: Map<String, Any?>): BugReportSummary =
        mapper.convertValue(entry, BugReportSummary::class.java)

    private fun coerceDetail(data: Map<String, Any?>): BugReportDetail =
        mapper.convertValue(data, BugReportDetail::class.java)

    @Suppress("UNCHECKED_CAST")
    private fun readIndex(): MutableMap<String, Any?> {
        if (indexPath.notExists()) {
            return mutableMapOf(
                "reports" to mutableListOf<Map<String, Any?>>(),
                "next_number" to 1,
            )
        }
        return try {
            val raw = mapper.readValue<MutableMap<String, Any?>>(indexPath.toFile())
            raw.getOrPut("reports") { mutableListOf<Map<String, Any?>>() }
            raw.getOrPut("next_number") {
                ((raw["reports"] as? List<*>)?.size ?: 0) + 1
            }
            // Coerce reports list into mutable for in-place ops below.
            raw["reports"] = (raw["reports"] as List<Map<String, Any?>>).toMutableList()
            raw
        } catch (e: Exception) {
            mutableMapOf(
                "reports" to mutableListOf<Map<String, Any?>>(),
                "next_number" to 1,
            )
        }
    }

    private fun writeIndex(index: Map<String, Any?>) {
        atomicWriteText(indexPath, mapper.writerWithDefaultPrettyPrinter().writeValueAsString(index))
    }

    @Suppress("UNCHECKED_CAST")
    private fun readReport(reportId: String): Map<String, Any?>? {
        val primary = storageDir.resolve("$reportId.json")
        if (primary.exists()) return mapper.readValue(primary.toFile())
        val archived = archiveDir.resolve("$reportId.json")
        if (archived.exists()) return mapper.readValue(archived.toFile())
        return null
    }

    private fun writeReport(reportId: String, data: Map<String, Any?>) {
        val live = storageDir.resolve("$reportId.json")
        val target = if (live.exists()) live
        else {
            val archived = archiveDir.resolve("$reportId.json")
            if (archived.exists()) archived else live
        }
        atomicWriteText(target, mapper.writerWithDefaultPrettyPrinter().writeValueAsString(data))
    }

    private fun writeScreenshot(reportId: String, screenshotBytes: ByteArray) {
        val path = storageDir.resolve("$reportId.png")
        atomicWriteBytes(path, screenshotBytes)
    }

    private fun updateIndexEntry(reportId: String, fields: Map<String, Any?>) {
        val index = readIndex()
        @Suppress("UNCHECKED_CAST")
        val reports = index["reports"] as MutableList<MutableMap<String, Any?>>
        // The mutable-cast above tolerates the immutable maps yielded by
        // Jackson's deserialization — we mutate by replacing entries.
        val updated = reports.map { row ->
            if (row["id"] == reportId) row.toMutableMap().also { it.putAll(fields) } else row
        }.toMutableList()
        index["reports"] = updated
        writeIndex(index)
    }

    private fun candidatePaths(reportId: String): List<Path> = listOf(
        storageDir.resolve("$reportId.json"),
        storageDir.resolve("$reportId.png"),
        archiveDir.resolve("$reportId.json"),
        archiveDir.resolve("$reportId.png"),
    )

    private fun archiveOne(reportId: String): Boolean {
        val jsonSrc = storageDir.resolve("$reportId.json")
        val pngSrc = storageDir.resolve("$reportId.png")
        if (jsonSrc.notExists() && pngSrc.notExists()) return false
        if (jsonSrc.exists()) {
            Files.move(jsonSrc, archiveDir.resolve("$reportId.json"), StandardCopyOption.REPLACE_EXISTING)
        }
        if (pngSrc.exists()) {
            Files.move(pngSrc, archiveDir.resolve("$reportId.png"), StandardCopyOption.REPLACE_EXISTING)
        }
        val index = readIndex()
        @Suppress("UNCHECKED_CAST")
        val reports = index["reports"] as MutableList<Map<String, Any?>>
        reports.removeAll { it["id"] == reportId }
        writeIndex(index)
        return true
    }

    private fun nowIso(): String =
        OffsetDateTime.now(ZoneOffset.UTC).toString()

    private fun atomicWriteText(path: Path, payload: String) {
        val tmp = path.resolveSibling("${path.fileName}.tmp")
        Files.writeString(tmp, payload)
        Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    private fun atomicWriteBytes(path: Path, payload: ByteArray) {
        val tmp = path.resolveSibling("${path.fileName}.tmp")
        Files.write(tmp, payload)
        Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }
}

private val REPORT_ID_REGEX = Regex("^bug-[A-Za-z]?\\d{3,}$")

internal fun isValidReportId(reportId: String): Boolean =
    REPORT_ID_REGEX.matches(reportId)
