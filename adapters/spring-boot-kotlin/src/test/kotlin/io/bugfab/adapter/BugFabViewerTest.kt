package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import io.bugfab.adapter.bootapp.TestApplication
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.core.env.Environment
import org.springframework.http.MediaType
import org.springframework.mock.web.MockMultipartFile
import org.springframework.test.context.TestPropertySource
import java.nio.file.Files
import java.nio.file.Path
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * Viewer endpoint conformance — list, detail, screenshot, status,
 * delete, bulk operations.
 *
 * Each test seeds at least one report via the intake endpoint so we
 * exercise the full storage roundtrip rather than poking the storage
 * bean directly. The seed PNG is intentionally tiny (8 bytes — just
 * the signature) because we're not checking image contents.
 */
@SpringBootTest(classes = [TestApplication::class])
@AutoConfigureMockMvc
@TestPropertySource(
    properties = [
        "bugfab.storage=file",
        "bugfab.storage-dir=#{T(java.nio.file.Files).createTempDirectory('bugfab-viewer-test').toString()}",
        "bugfab.rate-limit.enabled=false",
    ]
)
class BugFabViewerTest @Autowired constructor(
    private val mockMvc: MockMvc,
    private val mapper: ObjectMapper,
    private val env: Environment,
) {

    // Spring caches a single context per test class, and the SpEL
    // expression in `@TestPropertySource` evaluates once when the
    // context is built — so every test in this class shares one storage
    // directory. The list/stats/bulk cases assume a clean slate, so we
    // wipe the directory before each test rather than recreating the
    // context (which interferes with bean lifecycle in unexpected ways
    // — `@DirtiesContext(AFTER_EACH_TEST_METHOD)` only re-evaluates
    // SpEL when the test runner triggers a fresh context build, which
    // it skips for the first test of every run).
    @BeforeEach
    fun cleanStorage() {
        val dir = env.getProperty("bugfab.storage-dir")?.let { Path.of(it) } ?: return
        if (!Files.exists(dir)) return
        // Wipe report files but keep the directory tree intact so the
        // FileStorage bean (created once with the context) still has its
        // pre-checked `archive/` subdirectory available for bulk-archive.
        Files.walk(dir).use { stream ->
            stream.filter { Files.isRegularFile(it) }
                .forEach { Files.deleteIfExists(it) }
        }
        Files.createDirectories(dir.resolve("archive"))
    }

    private val pngSignature = byteArrayOf(
        0x89.toByte(), 0x50.toByte(), 0x4E.toByte(), 0x47.toByte(),
        0x0D.toByte(), 0x0A.toByte(), 0x1A.toByte(), 0x0A.toByte(),
    )

    private fun seed(title: String = "Test bug", severity: String = "medium"): String {
        val metadata = mapOf(
            "protocol_version" to "0.1",
            "title" to title,
            "client_ts" to "2026-04-27T15:29:58Z",
            "severity" to severity,
            "context" to mapOf("url" to "https://example.com/", "module" to "test"),
        )
        val response = mockMvc.perform(
            multipart("/bug-fab/bug-reports")
                .file(MockMultipartFile("screenshot", "s.png", "image/png", pngSignature.copyOf(64)))
                .param("metadata", mapper.writeValueAsString(metadata))
        )
            .andExpect(status().isCreated)
            .andReturn()
        @Suppress("UNCHECKED_CAST")
        val body = mapper.readValue(response.response.contentAsString, Map::class.java) as Map<String, Any?>
        return body["id"] as String
    }

    @Test
    fun `list returns paginated envelope with stats`() {
        seed("First bug")
        seed("Second bug")
        mockMvc.perform(get("/bug-fab/reports"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.items").isArray)
            .andExpect(jsonPath("$.total").value(2))
            .andExpect(jsonPath("$.stats.open").value(2))
            .andExpect(jsonPath("$.stats.fixed").value(0))
    }

    @Test
    fun `detail returns 404 for unknown id`() {
        mockMvc.perform(get("/bug-fab/reports/bug-999"))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `detail returns 404 for path-traversal style id`() {
        mockMvc.perform(get("/bug-fab/reports/..%2Fetc%2Fpasswd"))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `screenshot endpoint serves bytes as image-png`() {
        val id = seed()
        mockMvc.perform(get("/bug-fab/reports/$id/screenshot"))
            .andExpect(status().isOk)
            .andExpect { mvc ->
                val type = mvc.response.contentType ?: ""
                check(type.startsWith("image/png")) { "expected image/png, got $type" }
            }
    }

    @Test
    fun `status update changes status and appends lifecycle entry`() {
        val id = seed()
        val body = mapOf("status" to "fixed", "fix_commit" to "abc123")
        mockMvc.perform(
            put("/bug-fab/reports/$id/status")
                .contentType(MediaType.APPLICATION_JSON)
                .content(mapper.writeValueAsBytes(body))
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.status").value("fixed"))
            .andExpect(jsonPath("$.lifecycle[1].action").value("status_changed"))
    }

    @Test
    fun `status update rejects unknown status with 422`() {
        val id = seed()
        val body = mapOf("status" to "wontfix")
        mockMvc.perform(
            put("/bug-fab/reports/$id/status")
                .contentType(MediaType.APPLICATION_JSON)
                .content(mapper.writeValueAsBytes(body))
        )
            .andExpect(status().isUnprocessableEntity)
    }

    @Test
    fun `delete removes a report`() {
        val id = seed()
        mockMvc.perform(delete("/bug-fab/reports/$id"))
            .andExpect(status().isNoContent)
        mockMvc.perform(get("/bug-fab/reports/$id"))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `bulk-close-fixed transitions all fixed reports to closed`() {
        val first = seed("To close")
        val second = seed("Still open")
        // Transition only the first to fixed.
        mockMvc.perform(
            put("/bug-fab/reports/$first/status")
                .contentType(MediaType.APPLICATION_JSON)
                .content(mapper.writeValueAsBytes(mapOf("status" to "fixed")))
        )
        mockMvc.perform(post("/bug-fab/bulk-close-fixed"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.closed").value(1))
        mockMvc.perform(get("/bug-fab/reports/$first"))
            .andExpect(jsonPath("$.status").value("closed"))
        mockMvc.perform(get("/bug-fab/reports/$second"))
            .andExpect(jsonPath("$.status").value("open"))
    }

    @Test
    fun `bulk-archive-closed excludes archived reports from list`() {
        val id = seed("To archive")
        mockMvc.perform(
            put("/bug-fab/reports/$id/status")
                .contentType(MediaType.APPLICATION_JSON)
                .content(mapper.writeValueAsBytes(mapOf("status" to "closed")))
        )
        mockMvc.perform(post("/bug-fab/bulk-archive-closed"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.archived").value(1))
    }
}
