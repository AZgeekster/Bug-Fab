package io.bugfab.adapter.jpatest

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.autoconfigure.domain.EntityScan

/**
 * Dedicated bootstrap for the JPA storage test.
 *
 * Lives in a sub-package (not `io.bugfab.adapter`) for the same reason
 * as the file-backend `TestApplication`: the default `@ComponentScan`
 * of `@SpringBootApplication` must NOT scan the adapter package
 * directly. The adapter is wired via `AutoConfiguration.imports` only,
 * which lets `@ConditionalOnProperty(havingValue = "jpa")` on
 * `BugFabJpaConfiguration` actually gate the JPA wiring.
 *
 * [EntityScan] points at the adapter package so Hibernate picks up
 * `BugFabReportEntity`. The repository scan itself is handled by
 * `BugFabJpaConfiguration`'s own `@EnableJpaRepositories` — declaring
 * it here too would double-register the repository bean and trip
 * Spring's bean-definition override guard.
 */
@SpringBootApplication
@EntityScan(basePackages = ["io.bugfab.adapter"])
class JpaBootApp
