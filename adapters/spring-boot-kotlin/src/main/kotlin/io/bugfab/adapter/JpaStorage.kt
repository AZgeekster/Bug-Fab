package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import jakarta.persistence.Column
import jakarta.persistence.Entity
import jakarta.persistence.GeneratedValue
import jakarta.persistence.GenerationType
import jakarta.persistence.Id
import jakarta.persistence.Lob
import jakarta.persistence.LockModeType
import jakarta.persistence.Table
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.data.jpa.repository.Lock
import org.springframework.data.jpa.repository.Modifying
import org.springframework.data.jpa.repository.Query
import org.springframework.data.repository.query.Param
import org.springframework.transaction.annotation.Transactional

/**
 * JPA-backed storage. One table with the full report stored as a JSON
 * blob, plus screenshot bytes in a `@Lob` column. Three indexable
 * columns (`status`, `severity`, `module`) are denormalized so filter
 * queries don't have to crack the JSON.
 *
 * The schema deliberately stays simple. Consumers who outgrow it can
 * migrate to a richer schema with Flyway (recommended) or Liquibase —
 * see `MIGRATION_NOTES.md` § "JPA migrations".
 *
 * Lifecycle audit events are also persisted as JSON inside the same
 * blob (matching the file backend's layout). A separate
 * `bug_fab_lifecycle` table would let SQL queries reason about
 * lifecycle directly, but v0.1 leans on the file-backend symmetry —
 * the v0.2 schema rev can normalize.
 */
@Entity
@Table(name = "bug_fab_reports")
class BugFabReportEntity {
    @Id
    @Column(name = "id", length = 32)
    lateinit var id: String

    @Column(name = "status", length = 32, nullable = false)
    var status: String = "open"

    @Column(name = "severity", length = 32, nullable = false)
    var severity: String = "medium"

    @Column(name = "module", length = 256, nullable = false)
    var module: String = ""

    @Column(name = "environment", length = 64, nullable = false)
    var environment: String = ""

    @Column(name = "created_at", length = 64, nullable = false)
    var createdAt: String = ""

    /** Full report JSON. Authoritative source — denormalized cols mirror it. */
    @Lob
    @Column(name = "payload_json", nullable = false)
    var payloadJson: String = "{}"

    @Lob
    @Column(name = "screenshot", nullable = true)
    var screenshot: ByteArray? = null

    @Column(name = "archived", nullable = false)
    var archived: Boolean = false
}

interface BugFabReportRepository : JpaRepository<BugFabReportEntity, String> {

    @Query(
        """
        SELECT r FROM BugFabReportEntity r
        WHERE r.archived = false
          AND (:status IS NULL OR r.status = :status)
          AND (:severity IS NULL OR r.severity = :severity)
          AND (:module IS NULL OR r.module = :module)
          AND (:environment IS NULL OR r.environment = :environment)
        ORDER BY r.createdAt DESC
        """
    )
    fun search(
        @Param("status") status: String?,
        @Param("severity") severity: String?,
        @Param("module") module: String?,
        @Param("environment") environment: String?,
    ): List<BugFabReportEntity>

    @Query("SELECT r.status, COUNT(r) FROM BugFabReportEntity r WHERE r.archived = false GROUP BY r.status")
    fun statusCounts(): List<Array<Any>>

    fun findAllByStatusAndArchivedFalse(status: String): List<BugFabReportEntity>

    @Modifying
    @Transactional
    @Query("UPDATE BugFabReportEntity r SET r.archived = true WHERE r.status = :status AND r.archived = false")
    fun archiveByStatus(@Param("status") status: String): Int
}

/**
 * Single-row counter that mints sequential `bug-NNN` ids. Replaces the
 * process-local `AtomicLong` that could collide across JVM instances and
 * reissue a deleted id. Incremented under a pessimistic write lock inside
 * the insert transaction so concurrent submissions serialize.
 */
@Entity
@Table(name = "bug_fab_id_counter")
class BugFabIdCounterEntity {
    @Id
    @Column(name = "id")
    var id: Int = 1

    @Column(name = "last_value", nullable = false)
    var lastValue: Long = 0
}

interface BugFabIdCounterRepository : JpaRepository<BugFabIdCounterEntity, Int> {

    // SELECT ... FOR UPDATE on the single counter row. Valid on both H2
    // (tests) and Postgres (prod); the lock is held for the enclosing
    // transaction so the read-increment-write cannot lose an update.
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT c FROM BugFabIdCounterEntity c WHERE c.id = 1")
    fun findAndLock(): BugFabIdCounterEntity?
}

// `open` is required because the methods are annotated `@Transactional`
// and Spring proxies them with CGLIB by default (Spring Boot 2.x+ set
// `spring.aop.proxy-target-class=true`). Kotlin classes are final by
// default; the `kotlin("plugin.spring")` compiler plugin only opens
// classes that are themselves annotated with a Spring stereotype, and
// this class is registered via a `@Bean` factory method rather than a
// stereotype annotation — so we open it explicitly.
open class JpaStorage(
    private val repository: BugFabReportRepository,
    private val counterRepository: BugFabIdCounterRepository,
    private val idPrefix: String = "",
) : Storage {

    private val mapper: ObjectMapper = jacksonObjectMapper()

    @Transactional
    override fun saveReport(metadata: Map<String, Any?>, screenshotBytes: ByteArray): String {
        // Allocate from the counter row, not COUNT(*)/max: a delete must not
        // rewind the sequence and reissue a live id. The row is fetched under
        // a pessimistic write lock held for this transaction, so concurrent
        // submissions serialize on it rather than racing an in-JVM AtomicLong.
        val counter = counterRepository.findAndLock() ?: run {
            // Seed once from the highest existing id so a DB that predates the
            // counter (e.g. rows minted by the old AtomicLong) doesn't reissue
            // a taken id.
            val maxExisting = repository.findAll().mapNotNull { parseSeq(it.id) }.maxOrNull() ?: 0L
            counterRepository.saveAndFlush(BugFabIdCounterEntity().apply { lastValue = maxExisting })
        }
        counter.lastValue += 1
        counterRepository.save(counter)
        val seq = counter.lastValue
        val id = "bug-${idPrefix}${"%03d".format(seq)}"
        val now = nowIso()
        val report = buildReport(id, metadata, now)
        val entity = BugFabReportEntity().apply {
            this.id = id
            this.status = report["status"] as String
            this.severity = report["severity"] as String
            this.module = report["module"] as String
            this.environment = report["environment"] as String
            this.createdAt = now
            this.payloadJson = mapper.writeValueAsString(report)
            this.screenshot = screenshotBytes
        }
        repository.save(entity)
        return id
    }

    @Transactional(readOnly = true)
    override fun getReport(reportId: String): BugReportDetail? {
        if (!isValidReportId(reportId)) return null
        val entity = repository.findById(reportId).orElse(null) ?: return null
        return mapper.readValue<Map<String, Any?>>(entity.payloadJson)
            .let { mapper.convertValue(it, BugReportDetail::class.java) }
    }

    @Transactional(readOnly = true)
    override fun listReports(
        filters: Map<String, String>,
        page: Int,
        pageSize: Int,
    ): Pair<List<BugReportSummary>, Int> {
        val results = repository.search(
            filters["status"]?.takeIf { it.isNotBlank() },
            filters["severity"]?.takeIf { it.isNotBlank() },
            filters["module"]?.takeIf { it.isNotBlank() },
            filters["environment"]?.takeIf { it.isNotBlank() },
        )
        val total = results.size
        val start = ((page - 1) * pageSize).coerceAtLeast(0)
        val end = (start + pageSize).coerceAtMost(total)
        val pageEntities = if (start >= total) emptyList() else results.subList(start, end)
        val items = pageEntities.map { entity ->
            val raw = mapper.readValue<Map<String, Any?>>(entity.payloadJson)
            mapper.convertValue(raw, BugReportSummary::class.java)
        }
        return items to total
    }

    @Transactional(readOnly = true)
    override fun getScreenshotBytes(reportId: String): ByteArray? {
        if (!isValidReportId(reportId)) return null
        return repository.findById(reportId).orElse(null)?.screenshot
    }

    @Transactional
    override fun updateStatus(
        reportId: String,
        status: String,
        fixCommit: String,
        fixDescription: String,
        by: String,
    ): BugReportDetail? {
        if (!isValidReportId(reportId)) return null
        val entity = repository.findById(reportId).orElse(null) ?: return null
        val data = mapper.readValue<MutableMap<String, Any?>>(entity.payloadJson)
        val now = nowIso()
        data["status"] = status
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
        entity.status = status
        entity.payloadJson = mapper.writeValueAsString(data)
        repository.save(entity)
        return mapper.convertValue(data, BugReportDetail::class.java)
    }

    @Transactional
    override fun deleteReport(reportId: String): Boolean {
        if (!isValidReportId(reportId)) return false
        if (!repository.existsById(reportId)) return false
        repository.deleteById(reportId)
        return true
    }

    @Transactional
    override fun bulkCloseFixed(by: String): Int {
        val ids = repository.findAllByStatusAndArchivedFalse("fixed").map { it.id }
        var closed = 0
        for (id in ids) {
            if (updateStatus(id, "closed", by = by) != null) closed++
        }
        return closed
    }

    @Transactional
    override fun bulkArchiveClosed(): Int = repository.archiveByStatus("closed")

    @Transactional
    override fun setGithubLink(
        reportId: String,
        issueNumber: Long,
        issueUrl: String,
    ): BugReportDetail? {
        if (!isValidReportId(reportId)) return null
        val entity = repository.findById(reportId).orElse(null) ?: return null
        val data = mapper.readValue<MutableMap<String, Any?>>(entity.payloadJson)
        data["github_issue_number"] = issueNumber
        data["github_issue_url"] = issueUrl
        entity.payloadJson = mapper.writeValueAsString(data)
        repository.save(entity)
        return mapper.convertValue(data, BugReportDetail::class.java)
    }

    @Transactional(readOnly = true)
    override fun computeStats(): Map<String, Int> {
        val raw = repository.statusCounts().associate {
            (it[0] as String) to (it[1] as Long).toInt()
        }
        return mapOf(
            "open" to (raw["open"] ?: 0),
            "investigating" to (raw["investigating"] ?: 0),
            "fixed" to (raw["fixed"] ?: 0),
            "closed" to (raw["closed"] ?: 0),
        )
    }

    private fun parseSeq(id: String): Long? {
        // bug-001, bug-P038 — pull trailing digits.
        val match = Regex("""\d+$""").find(id) ?: return null
        return match.value.toLongOrNull()
    }
}
