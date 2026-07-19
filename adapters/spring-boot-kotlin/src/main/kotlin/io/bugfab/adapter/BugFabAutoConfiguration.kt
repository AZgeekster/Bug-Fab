package io.bugfab.adapter

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.PropertyNamingStrategies
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import jakarta.persistence.EntityManagerFactory
import org.springframework.boot.autoconfigure.AutoConfiguration
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Import
import org.springframework.data.jpa.repository.config.EnableJpaRepositories
import org.springframework.web.servlet.config.annotation.PathMatchConfigurer
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer
import java.nio.file.Path
import java.util.function.Predicate

/**
 * Spring Boot auto-configuration for the Bug-Fab adapter.
 *
 * Consumers add this dependency to their build and the eight endpoints
 * appear under the configured `bugfab.routePrefix` (default `/bug-fab`).
 * No `@EnableBugFab` annotation is needed — Spring Boot's
 * auto-configuration mechanism wires everything from
 * `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`.
 *
 * Route prefix wiring uses Spring MVC's [PathMatchConfigurer.addPathPrefix]
 * so the controller's `@PostMapping("/bug-reports")` becomes
 * `${bugfab.routePrefix}/bug-reports` at runtime — without forcing every
 * mapping to repeat the prefix string.
 *
 * Storage selection: `bugfab.storage=file` (default) wires [FileStorage];
 * `bugfab.storage=jpa` wires [JpaStorage] and the JPA repository support.
 * Consumers who set `bugfab.storage=jpa` MUST also supply a DataSource
 * (Spring Boot auto-configures one if the classpath has a driver).
 *
 * Conditional ordering note: this auto-configuration runs AFTER
 * [HibernateJpaAutoConfiguration] so the JPA `EntityManagerFactory` is
 * available when we build the JpaStorage bean. See "Auto-configuration
 * ordering vs Spring Security" in MIGRATION_NOTES.md for the gotcha
 * around method-security advice.
 */
@AutoConfiguration(after = [HibernateJpaAutoConfiguration::class])
@EnableConfigurationProperties(BugFabProperties::class)
@Import(BugFabExceptionHandler::class, BugFabController::class)
class BugFabAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    fun bugFabObjectMapper(): ObjectMapper =
        jacksonObjectMapper().apply {
            propertyNamingStrategy = PropertyNamingStrategies.SNAKE_CASE
        }

    @Bean
    @ConditionalOnMissingBean
    @ConditionalOnProperty(prefix = "bugfab", name = ["storage"], havingValue = "file", matchIfMissing = true)
    fun fileStorage(properties: BugFabProperties): Storage =
        FileStorage(Path.of(properties.storageDir), properties.idPrefix)

    @Bean
    @ConditionalOnMissingBean
    @ConditionalOnProperty(prefix = "bugfab", name = ["rate-limit.enabled"], havingValue = "true")
    fun bugFabRateLimiter(properties: BugFabProperties): BugFabRateLimiter =
        BugFabRateLimiter(
            maxPerWindow = properties.rateLimit.maxPerWindow,
            windowSeconds = properties.rateLimit.windowSeconds.toLong(),
        )

    @Bean
    fun bugFabPathPrefix(properties: BugFabProperties): WebMvcConfigurer = object : WebMvcConfigurer {
        override fun configurePathMatch(configurer: PathMatchConfigurer) {
            // Apply the configured prefix to every method on BugFabController.
            // This keeps the controller annotations clean (relative paths)
            // while letting consumers mount Bug-Fab anywhere under their app.
            val prefix = properties.routePrefix.trimEnd('/')
            if (prefix.isNotEmpty()) {
                configurer.addPathPrefix(prefix, Predicate { type -> type == BugFabController::class.java })
            }
        }
    }
}

/**
 * Optional sub-configuration that opts the consumer into the JPA backend.
 *
 * Kept separate from [BugFabAutoConfiguration] so consumers who choose
 * `bugfab.storage=file` don't pay the cost of Spring Data JPA repository
 * scanning. Consumers selecting JPA pick up this configuration via the
 * `@ConditionalOnProperty` guard plus the auto-import file.
 */
@Configuration
@ConditionalOnClass(EntityManagerFactory::class)
@ConditionalOnProperty(prefix = "bugfab", name = ["storage"], havingValue = "jpa")
@EnableJpaRepositories(basePackageClasses = [BugFabReportRepository::class])
class BugFabJpaConfiguration {

    @Bean
    fun jpaStorage(
        repository: BugFabReportRepository,
        counterRepository: BugFabIdCounterRepository,
        properties: BugFabProperties,
    ): Storage = JpaStorage(repository, counterRepository, properties.idPrefix)
}
