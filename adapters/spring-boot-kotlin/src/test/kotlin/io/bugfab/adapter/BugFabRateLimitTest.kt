package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import io.bugfab.adapter.bootapp.TestApplication
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
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
            .file(
                MockMultipartFile(
                    "metadata", "metadata", MediaType.APPLICATION_JSON_VALUE,
                    mapper.writeValueAsBytes(
                        mapOf(
                            "protocol_version" to "0.1",
                            "title" to "RL test",
                            "client_ts" to "2026-04-27T00:00:00Z",
                        )
                    )
                )
            )
            .file(MockMultipartFile("screenshot", "s.png", "image/png", pngSig.copyOf(64)))
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
}
