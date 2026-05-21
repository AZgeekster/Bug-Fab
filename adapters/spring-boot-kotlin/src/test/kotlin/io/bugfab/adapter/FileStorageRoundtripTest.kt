package io.bugfab.adapter

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Path

/**
 * Direct storage-layer tests. These run without Spring context so they
 * exercise the file backend's logic — id assignment, atomic writes,
 * archive moves, bulk transitions — in isolation.
 *
 * Network-level conformance lives in the MockMvc tests; this suite is
 * the equivalent of the Python reference's `tests/storage/test_files.py`.
 */
class FileStorageRoundtripTest {

    private fun makeMetadata(title: String = "Sample"): Map<String, Any?> = mapOf(
        "protocol_version" to "0.1",
        "title" to title,
        "client_ts" to "2026-04-27T00:00:00Z",
        "severity" to "medium",
        "context" to mapOf("module" to "test", "environment" to "dev"),
    )

    private val pngBytes = byteArrayOf(
        0x89.toByte(), 0x50.toByte(), 0x4E.toByte(), 0x47.toByte(),
        0x0D.toByte(), 0x0A.toByte(), 0x1A.toByte(), 0x0A.toByte(),
        0, 0, 0, 0,
    )

    @Test
    fun `save assigns bug-NNN id and round-trips full report`(@TempDir tmp: Path) {
        val storage = FileStorage(tmp)
        val id = storage.saveReport(makeMetadata("First"), pngBytes)
        assertTrue(Regex("^bug-\\d{3,}$").matches(id), "Expected bug-NNN id, got $id")
        val detail = storage.getReport(id)
        assertNotNull(detail)
        assertEquals("First", detail!!.title)
        assertEquals("open", detail.status)
        assertEquals("0.1", detail.protocolVersion)
    }

    @Test
    fun `id prefix is honored`(@TempDir tmp: Path) {
        val storage = FileStorage(tmp, idPrefix = "P")
        val id = storage.saveReport(makeMetadata(), pngBytes)
        assertTrue(id.startsWith("bug-P"))
    }

    @Test
    fun `list filters by status`(@TempDir tmp: Path) {
        val storage = FileStorage(tmp)
        val id1 = storage.saveReport(makeMetadata("Open"), pngBytes)
        storage.saveReport(makeMetadata("Also open"), pngBytes)
        storage.updateStatus(id1, "fixed")
        val (fixedItems, fixedTotal) = storage.listReports(mapOf("status" to "fixed"), 1, 20)
        assertEquals(1, fixedTotal)
        assertEquals(id1, fixedItems[0].id)
    }

    @Test
    fun `delete removes screenshot and metadata`(@TempDir tmp: Path) {
        val storage = FileStorage(tmp)
        val id = storage.saveReport(makeMetadata(), pngBytes)
        assertTrue(storage.deleteReport(id))
        assertNull(storage.getReport(id))
        assertNull(storage.getScreenshotBytes(id))
    }

    @Test
    fun `bulkArchiveClosed moves closed reports into archive dir`(@TempDir tmp: Path) {
        val storage = FileStorage(tmp)
        val id = storage.saveReport(makeMetadata(), pngBytes)
        storage.updateStatus(id, "closed")
        assertEquals(1, storage.bulkArchiveClosed())
        // The detail endpoint still resolves archived reports.
        assertNotNull(storage.getReport(id))
        // But the report no longer shows up in default listings.
        val (items, total) = storage.listReports(emptyMap(), 1, 20)
        assertEquals(0, total)
        assertTrue(items.isEmpty())
    }

    @Test
    fun `path traversal style ids are rejected at the storage layer`(@TempDir tmp: Path) {
        val storage = FileStorage(tmp)
        assertNull(storage.getReport("../etc/passwd"))
        assertNull(storage.getReport("bug-001/../../etc"))
    }
}
