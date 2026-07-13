package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import io.bugfab.adapter.jpatest.JpaBootApp
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.mock.web.MockMultipartFile
import org.springframework.test.context.TestPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * End-to-end test against the JPA backend with an in-memory H2.
 *
 * The H2 URL uses a unique database name per test class to keep state
 * isolated even when the JVM is reused — `Mode=PostgreSQL` brings the
 * dialect closer to what consumers will see in real deployments so
 * subtle SQL portability bugs surface early.
 */
@SpringBootTest(classes = [JpaBootApp::class])
@AutoConfigureMockMvc
@TestPropertySource(
    properties = [
        "bugfab.storage=jpa",
        "bugfab.rate-limit.enabled=false",
        // Default H2 mode rather than `MODE=PostgreSQL`: PostgreSQL mode
        // rejects the `BLOB` column type that Hibernate generates for the
        // `@Lob ByteArray? screenshot` field, blocking schema creation.
        // The cross-dialect SQL portability check belongs in a separate
        // Testcontainers-backed integration test against real Postgres.
        "spring.datasource.url=jdbc:h2:mem:bugfabjpatest;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.H2Dialect",
    ]
)
class BugFabJpaStorageTest @Autowired constructor(
    private val mockMvc: MockMvc,
    private val mapper: ObjectMapper,
) {

    private val pngSig = byteArrayOf(
        0x89.toByte(), 0x50.toByte(), 0x4E.toByte(), 0x47.toByte(),
        0x0D.toByte(), 0x0A.toByte(), 0x1A.toByte(), 0x0A.toByte(),
    )

    @Test
    fun `jpa backend saves a report and serves it back`() {
        val metadata = mapOf(
            "protocol_version" to "0.1",
            "title" to "JPA roundtrip",
            "client_ts" to "2026-04-27T00:00:00Z",
            "severity" to "low",
        )
        val response = mockMvc.perform(
            multipart("/bug-fab/bug-reports")
                .file(MockMultipartFile("screenshot", "s.png", "image/png", pngSig.copyOf(64)))
                .param("metadata", mapper.writeValueAsString(metadata))
        )
            .andExpect(status().isCreated)
            .andReturn()
        @Suppress("UNCHECKED_CAST")
        val body = mapper.readValue(response.response.contentAsString, Map::class.java) as Map<String, Any?>
        val id = body["id"] as String

        mockMvc.perform(get("/bug-fab/reports/$id"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.title").value("JPA roundtrip"))
            .andExpect(jsonPath("$.severity").value("low"))
    }
}
