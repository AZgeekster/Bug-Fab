package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import jakarta.servlet.http.HttpServletRequest
import jakarta.validation.Valid
import jakarta.validation.Validator
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.multipart.MultipartFile

/**
 * REST controller for the eight Bug-Fab v0.1 wire-protocol endpoints.
 *
 * Path mapping is configured by [BugFabRoutePathProvider] — the route
 * prefix is resolved at startup from [BugFabProperties.routePrefix] and
 * injected via the request mapping in [BugFabAutoConfiguration].
 *
 * Why a single controller class: every endpoint shares the same
 * storage + properties + rate-limiter dependencies. Splitting one
 * endpoint per class (the ASP.NET reference's style) is more idiomatic
 * in C# minimal APIs but is overkill for Spring MVC, where method-level
 * mappings on a single `@RestController` is the conventional pattern.
 *
 * Validation order — mirrors `bug_fab/routers/submit.py`:
 *   1. Rate-limit check (cheap; reject early)
 *   2. Metadata JSON parse (`400 validation_error` on malformed JSON)
 *   3. Bean Validation on `BugReportCreate` (`422 schema_error`)
 *   4. Screenshot size check (`413 payload_too_large`)
 *   5. PNG magic-byte sniff (`415 unsupported_media_type`)
 *   6. Save → respond
 */
@RestController
class BugFabController(
    private val storage: Storage,
    private val properties: BugFabProperties,
    @Autowired(required = false) private val rateLimiter: BugFabRateLimiter?,
    private val validator: Validator,
    private val mapper: ObjectMapper,
) {

    private val pngSignature = byteArrayOf(
        0x89.toByte(), 0x50.toByte(), 0x4E.toByte(), 0x47.toByte(),
        0x0D.toByte(), 0x0A.toByte(), 0x1A.toByte(), 0x0A.toByte(),
    )

    // -------- INTAKE --------

    /**
     * Submit a new bug report.
     *
     * Note on multipart handling: we accept the screenshot as a
     * `MultipartFile` but check `getSize()` and stream-read via
     * `getBytes()` only after the rate-limit and metadata-validation
     * gates. Spring will buffer multipart parts to disk when they
     * exceed `spring.servlet.multipart.file-size-threshold` (default
     * 0 → always disk-spool), so even oversize uploads don't put the
     * whole part into JVM heap. The hard cap from
     * `spring.servlet.multipart.max-file-size` is set in
     * `application.yml` to match `bugfab.maxScreenshotMb`; consumers
     * who override one MUST override both.
     */
    @PostMapping("/bug-reports", consumes = [MediaType.MULTIPART_FORM_DATA_VALUE])
    fun submit(
        @RequestParam("metadata") metadata: String,
        @RequestParam("screenshot") screenshot: MultipartFile,
        request: HttpServletRequest,
    ): ResponseEntity<Any> {
        // `metadata` is a plain multipart form FIELD carrying the JSON
        // string — the Bug-Fab JS bundle sends it as
        // `formData.append("metadata", json)` and the FastAPI reference
        // binds it with `Form(...)`, so it arrives with no filename.
        // Binding it as `MultipartFile` (the previous shape) rejected
        // every real submission with a framework-level 400.
        // 1. Rate limit (when enabled).
        if (rateLimiter != null) {
            val clientIp = resolveClientIp(request)
            if (!rateLimiter.check(clientIp)) {
                val rl = properties.rateLimit
                return ResponseEntity
                    .status(HttpStatus.TOO_MANY_REQUESTS)
                    .header("Retry-After", rl.windowSeconds.toString())
                    .body(
                        ErrorEnvelope(
                            error = "rate_limited",
                            detail = "Rate limit exceeded: max ${rl.maxPerWindow} reports per ${rl.windowSeconds} seconds",
                            retryAfterSeconds = rl.windowSeconds,
                        )
                    )
            }
        }

        // 2. Parse the metadata JSON. Malformed JSON is 400 (the consumer
        //    can distinguish "not parseable" from "parseable but invalid").
        val rawMetadata: Map<String, Any?> = try {
            mapper.readValue(metadata)
        } catch (e: Exception) {
            return badRequest("metadata is not valid JSON: ${e.message ?: "unknown"}")
        }

        // 3. Protocol version check — explicit because the contract uses
        //    a distinct error code (`unsupported_protocol_version`).
        val protocolVersion = rawMetadata["protocol_version"] as? String
        if (protocolVersion == null) {
            return badRequest("metadata.protocol_version is required")
        }
        if (protocolVersion != "0.1") {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(
                    ErrorEnvelope(
                        error = "unsupported_protocol_version",
                        detail = "Protocol version '$protocolVersion' is not supported by this adapter (expected '0.1')",
                    )
                )
        }

        // 4. Bean Validation. Convert to BugReportCreate first; Jackson's
        //    enum coercion already enforces severity vocabulary, so an
        //    invalid value lands in the IllegalArgumentException catch
        //    below (mapped to 422 in BugFabExceptionHandler).
        val payload: BugReportCreate = try {
            mapper.convertValue(rawMetadata, BugReportCreate::class.java)
        } catch (e: Exception) {
            return unprocessable(e.message ?: "metadata schema validation failed")
        }
        val violations = validator.validate(payload)
        if (violations.isNotEmpty()) {
            return unprocessable(
                violations.joinToString("; ") { "${it.propertyPath}: ${it.message}" }
            )
        }

        // 5. Screenshot size + magic-byte check.
        val maxBytes = properties.maxScreenshotMb.toLong() * 1024 * 1024
        if (screenshot.size > maxBytes) {
            return ResponseEntity.status(HttpStatus.PAYLOAD_TOO_LARGE)
                .body(
                    ErrorEnvelope(
                        error = "payload_too_large",
                        detail = "Screenshot exceeds maximum size of ${properties.maxScreenshotMb} MiB",
                        limitBytes = maxBytes,
                    )
                )
        }
        val screenshotBytes = screenshot.bytes
        if (screenshotBytes.isEmpty()) {
            return badRequest("Screenshot file is empty")
        }
        if (!isPng(screenshotBytes)) {
            return ResponseEntity.status(HttpStatus.UNSUPPORTED_MEDIA_TYPE)
                .body(
                    ErrorEnvelope(
                        error = "unsupported_media_type",
                        detail = "Screenshot must be a PNG image (image/png)",
                    )
                )
        }

        // 6. Build the metadata dict we hand to storage. The server is
        //    authoritative for User-Agent, environment, and protocol
        //    version — the client's value is preserved as
        //    `client_reported_user_agent` separately.
        val storeMeta = rawMetadata.toMutableMap()
        storeMeta["server_user_agent"] = request.getHeader("User-Agent") ?: ""
        val ctxMap = (rawMetadata["context"] as? Map<*, *>) ?: emptyMap<String, Any?>()
        val envFromCtx = ctxMap["environment"] as? String ?: ""
        storeMeta["environment"] = (rawMetadata["environment"] as? String).takeUnless { it.isNullOrBlank() }
            ?: envFromCtx

        val reportId = storage.saveReport(storeMeta, screenshotBytes)
        val detail = storage.getReport(reportId)
            ?: return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ErrorEnvelope("internal_error", "Stored report could not be read back"))

        return ResponseEntity.status(HttpStatus.CREATED).body(
            BugReportIntakeResponse(
                id = reportId,
                receivedAt = detail.createdAt,
                storedAt = "bug-fab://reports/$reportId",
                githubIssueUrl = null,
            )
        )
    }

    // -------- VIEWER --------

    @GetMapping("/reports", produces = [MediaType.APPLICATION_JSON_VALUE])
    fun list(
        @RequestParam(required = false) status: String?,
        @RequestParam(required = false) severity: String?,
        @RequestParam(required = false) module: String?,
        @RequestParam(required = false) environment: String?,
        @RequestParam(defaultValue = "1") page: Int,
        @RequestParam(name = "page_size", defaultValue = "20") pageSize: Int,
    ): ResponseEntity<BugReportListResponse> {
        val effectivePageSize = pageSize.coerceIn(1, 200)
        val effectivePage = page.coerceAtLeast(1)
        val filters = buildFilters(status, severity, module, environment)
        val (items, total) = storage.listReports(filters, effectivePage, effectivePageSize)
        val stats = storage.computeStats()
        val statsOut = listOf("open", "investigating", "fixed", "closed")
            .associateWith { stats[it] ?: 0 }
        return ResponseEntity.ok(
            BugReportListResponse(
                items = items,
                total = total,
                page = effectivePage,
                pageSize = effectivePageSize,
                stats = statsOut,
            )
        )
    }

    @GetMapping("/reports/{reportId}", produces = [MediaType.APPLICATION_JSON_VALUE])
    fun detail(@PathVariable reportId: String): ResponseEntity<Any> {
        if (!isValidReportId(reportId)) return notFound()
        val report = storage.getReport(reportId) ?: return notFound()
        return ResponseEntity.ok(report)
    }

    @GetMapping("/reports/{reportId}/screenshot", produces = [MediaType.IMAGE_PNG_VALUE])
    fun screenshot(@PathVariable reportId: String): ResponseEntity<Any> {
        if (!isValidReportId(reportId)) return notFound()
        val bytes = storage.getScreenshotBytes(reportId) ?: return notFound()
        return ResponseEntity.ok()
            .contentType(MediaType.IMAGE_PNG)
            .body(bytes)
    }

    @PutMapping(
        "/reports/{reportId}/status",
        consumes = [MediaType.APPLICATION_JSON_VALUE],
        produces = [MediaType.APPLICATION_JSON_VALUE],
    )
    fun updateStatus(
        @PathVariable reportId: String,
        @RequestBody @Valid payload: BugReportStatusUpdate,
        request: HttpServletRequest,
    ): ResponseEntity<Any> {
        if (!properties.viewerPermissions.canEditStatus) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(
                ErrorEnvelope("forbidden", "viewer action 'can_edit_status' is disabled")
            )
        }
        if (!isValidReportId(reportId)) return notFound()
        val actor = request.getAttribute("bug_fab_actor") as? String ?: "viewer"
        val updated = storage.updateStatus(
            reportId,
            payload.status.wire,
            payload.fixCommit,
            payload.fixDescription,
            actor,
        ) ?: return notFound()
        return ResponseEntity.ok(updated)
    }

    @DeleteMapping("/reports/{reportId}")
    fun deleteReport(@PathVariable reportId: String): ResponseEntity<Any> {
        if (!properties.viewerPermissions.canDelete) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(
                ErrorEnvelope("forbidden", "viewer action 'can_delete' is disabled")
            )
        }
        if (!isValidReportId(reportId)) return notFound()
        return if (storage.deleteReport(reportId)) {
            ResponseEntity.status(HttpStatus.NO_CONTENT).build()
        } else {
            notFound()
        }
    }

    @PostMapping("/bulk-close-fixed", produces = [MediaType.APPLICATION_JSON_VALUE])
    fun bulkCloseFixed(request: HttpServletRequest): ResponseEntity<Any> {
        if (!properties.viewerPermissions.canBulk) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(
                ErrorEnvelope("forbidden", "viewer action 'can_bulk' is disabled")
            )
        }
        val actor = request.getAttribute("bug_fab_actor") as? String ?: "viewer"
        val closed = storage.bulkCloseFixed(actor)
        return ResponseEntity.ok(mapOf("closed" to closed))
    }

    @PostMapping("/bulk-archive-closed", produces = [MediaType.APPLICATION_JSON_VALUE])
    fun bulkArchiveClosed(): ResponseEntity<Any> {
        if (!properties.viewerPermissions.canBulk) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(
                ErrorEnvelope("forbidden", "viewer action 'can_bulk' is disabled")
            )
        }
        val archived = storage.bulkArchiveClosed()
        return ResponseEntity.ok(mapOf("archived" to archived))
    }

    // -------- helpers --------

    private fun isPng(bytes: ByteArray): Boolean {
        if (bytes.size < pngSignature.size) return false
        for (i in pngSignature.indices) {
            if (bytes[i] != pngSignature[i]) return false
        }
        return true
    }

    private fun buildFilters(
        status: String?,
        severity: String?,
        module: String?,
        environment: String?,
    ): Map<String, String> = buildMap {
        status?.takeIf { it.isNotBlank() }?.let { put("status", it.trim()) }
        severity?.takeIf { it.isNotBlank() }?.let { put("severity", it.trim()) }
        module?.takeIf { it.isNotBlank() }?.let { put("module", it.trim()) }
        environment?.takeIf { it.isNotBlank() }?.let { put("environment", it.trim()) }
    }

    private fun badRequest(detail: String) = ResponseEntity
        .status(HttpStatus.BAD_REQUEST)
        .body<Any>(ErrorEnvelope("validation_error", detail))

    private fun unprocessable(detail: Any) = ResponseEntity
        .status(HttpStatus.UNPROCESSABLE_ENTITY)
        .body<Any>(ErrorEnvelope("schema_error", detail))

    private fun notFound() = ResponseEntity
        .status(HttpStatus.NOT_FOUND)
        .body<Any>(ErrorEnvelope("not_found", "Bug report not found"))
}
