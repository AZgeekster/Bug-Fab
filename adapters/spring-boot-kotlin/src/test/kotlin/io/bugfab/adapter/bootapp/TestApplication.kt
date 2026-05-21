package io.bugfab.adapter.bootapp

import org.springframework.boot.autoconfigure.SpringBootApplication

/**
 * Minimal Spring Boot application used as the bootstrap class for the
 * file-backend `@SpringBootTest` cases.
 *
 * Lives in this dedicated sub-package on purpose: the default
 * `@ComponentScan` of `@SpringBootApplication` starts from the
 * annotated class's own package. By keeping this class OUT of
 * `io.bugfab.adapter`, the consumer-style scan never finds
 * `BugFabAutoConfiguration` / `BugFabJpaConfiguration` directly, and
 * the adapter is wired only via Spring Boot's auto-configuration import
 * file (`META-INF/spring/...AutoConfiguration.imports`). That's how a
 * real consumer would wire it, and it lets `@ConditionalOnProperty` on
 * the JPA configuration actually block the configuration when the
 * file backend is selected — direct component scan was registering
 * `@EnableJpaRepositories` regardless of the condition, which then
 * tried to wire a non-existent `entityManagerFactory`.
 */
@SpringBootApplication
class TestApplication
