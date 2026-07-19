package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import io.bugfab.adapter.bootapp.TestApplication
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.mock.web.MockMultipartFile
import org.springframework.test.context.TestPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.header
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * Rate-limit conformance. With the bucket sized at 2 per 60 seconds,
 * the third submission within the window MUST 429 with the documented
 * envelope shape ({error, detail, retry_after_seconds}).
 */
@SpringBootTest(classes = [TestApplication::class])
@AutoConfigureMockMvc
@TestPropertySource(
    properties = [
        "bugfab.storage=file",
        "bugfab.storage-dir=#{T(java.nio.file.Files).createTempDirectory('bugfab-rl-test').toString()}",
        "bugfab.rate-limit.enabled=true",
        "bugfab.rate-limit.max-per-window=2",
        "bugfab.rate-limit.window-seconds=60",
    ]
)
class BugFabRateLimitTest @Autowired constructor(
    private val mockMvc: MockMvc,
    private val mapper: ObjectMapper,
) {

    private val pngSig = byteArrayOf(
        0x89.toByte(), 0x50.toByte(), 0x4E.toByte(), 0x47.toByte(),
        0x0D.toByte(), 0x0A.toByte(), 0x1A.toByte(), 0x0A.toByte(),
    )

    private fun submit() = mockMvc.perform(
        multipart("/bug-fab/bug-reports")
            .file(MockMultipartFile("screenshot", "s.png", "image/png", pngSig.copyOf(64)))
            .param(
                "metadata",
                mapper.writeValueAsString(
                    mapOf(
                        "protocol_version" to "0.1",
                        "title" to "RL test",
                        "client_ts" to "2026-04-27T00:00:00Z",
                    )
                ),
            )
    )

    @Test
    fun `third submission within window returns 429 with envelope`() {
        submit().andExpect(status().isCreated)
        submit().andExpect(status().isCreated)
        submit()
            .andExpect(status().isTooManyRequests)
            .andExpect(header().exists("Retry-After"))
            .andExpect(jsonPath("$.error").value("rate_limited"))
            .andExpect(jsonPath("$.retry_after_seconds").value(60))
    }

    @Test
    fun `spoofed X-Forwarded-For from an untrusted peer cannot evade the limit`() {
        // trusted-proxies defaults to empty, so the header is ignored and
        // every request keys on the direct peer — rotating the header used
        // to mint a fresh bucket per request and defeat the limiter. A
        // distinct peer address keeps this test's bucket independent of the
        // sibling test (the Spring context, and thus the limiter, is shared).
        submitWithForwardedFor("10.0.0.1").andExpect(status().isCreated)
        submitWithForwardedFor("10.0.0.2").andExpect(status().isCreated)
        submitWithForwardedFor("10.0.0.3").andExpect(status().isTooManyRequests)
    }

    private fun submitWithForwardedFor(spoofed: String) = mockMvc.perform(
        multipart("/bug-fab/bug-reports")
            .file(MockMultipartFile("screenshot", "s.png", "image/png", pngSig.copyOf(64)))
            .param(
                "metadata",
                mapper.writeValueAsString(
                    mapOf(
                        "protocol_version" to "0.1",
                        "title" to "RL spoof test",
                        "client_ts" to "2026-04-27T00:00:00Z",
                    )
                ),
            )
            .header("X-Forwarded-For", spoofed)
            .with { req ->
                req.remoteAddr = "198.51.100.7"
                req
            }
    )
}

/** Pure-unit coverage for the limiter's eviction and the trust gate. */
class RateLimiterUnitTest {

    @Test
    fun `idle buckets are evicted after a full window`() {
        val limiter = BugFabRateLimiter(maxPerWindow = 5, windowSeconds = 1)
        limiter.check("1.1.1.1")
        limiter.check("2.2.2.2")
        org.junit.jupiter.api.Assertions.assertEquals(2, limiter.trackedKeys())
        Thread.sleep(1100)
        // The next check triggers the once-per-window sweep; the two idle
        // buckets go, leaving only the fresh key. Unbounded growth was the
        // memory-exhaustion sink the audit flagged.
        limiter.check("3.3.3.3")
        org.junit.jupiter.api.Assertions.assertEquals(1, limiter.trackedKeys())
    }

    @Test
    fun `forwarded header ignored from untrusted peer`() {
        val req = org.springframework.mock.web.MockHttpServletRequest()
        req.remoteAddr = "203.0.113.5"
        req.addHeader("X-Forwarded-For", "9.9.9.9")
        org.junit.jupiter.api.Assertions.assertEquals(
            "203.0.113.5", resolveClientIp(req, emptySet())
        )
    }

    @Test
    fun `forwarded header honored from trusted peer`() {
        val req = org.springframework.mock.web.MockHttpServletRequest()
        req.remoteAddr = "10.0.0.1"
        req.addHeader("X-Forwarded-For", "9.9.9.9, 7.7.7.7")
        org.junit.jupiter.api.Assertions.assertEquals(
            "9.9.9.9", resolveClientIp(req, setOf("10.0.0.1"))
        )
    }

    @Test
    fun `wildcard trusts every peer`() {
        val req = org.springframework.mock.web.MockHttpServletRequest()
        req.remoteAddr = "203.0.113.5"
        req.addHeader("X-Forwarded-For", "9.9.9.9")
        org.junit.jupiter.api.Assertions.assertEquals(
            "9.9.9.9", resolveClientIp(req, setOf("*"))
        )
    }
}
