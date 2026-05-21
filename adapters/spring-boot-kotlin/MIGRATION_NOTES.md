# Migration notes — Spring Boot Kotlin adapter

Things that surprised us porting from the FastAPI reference, plus gotchas Spring-ecosystem consumers will hit.

## Intake controller — metadata binds as `MultipartFile`, not `String`

The intake endpoint declares `metadata` as `@RequestPart("metadata") MultipartFile` rather than `@RequestParam("metadata") String`. This is the wire-protocol-correct binding: the JS bundle posts metadata as a part with `Content-Type: application/json` (file-style part), and Spring's `@RequestParam String` codec silently truncates JSON parts at the first newline / `;` depending on the configured `MessageConverter` chain. Reading the part as `MultipartFile` and decoding `getBytes()` through Jackson preserves the full JSON payload exactly as the JS bundle sent it.

Do not "simplify" this back to `@RequestParam String metadata`. The conformance suite catches the truncation case, but a casual refactor would re-introduce it.

## `JpaStorage` is `open class`

`JpaStorage` is declared `open class` (not `class`) so CGLIB can subclass it to apply `@Transactional` proxies. Kotlin's default `final` would cause Spring to fall back to JDK dynamic proxies, which only proxy interface methods — the storage trait we expose isn't an interface in v0.1, so `@Transactional` would be a silent no-op without `open`.

If a consumer subclasses `JpaStorage` to customize persistence, that's intentional and supported. Mark your overrides `override` as usual.

## Package-scan footgun — `BugFabJpaConfiguration` registration order

**Known issue, v0.2 cleanup.** If a consumer's `@SpringBootApplication` scans `io.bugfab.adapter` (the default if they put their main class in `io.bugfab.adapter.consumer` or any sub-package), `BugFabJpaConfiguration` gets picked up as a regular `@Configuration` *before* `@ConditionalOnProperty` evaluates its `@EnableJpaRepositories`. The symptom: JPA repositories try to wire even when `bugfab.storage=file`, causing startup failures if no JDBC driver is on the classpath.

**Workarounds for v0.1 consumers:**

1. Keep your `@SpringBootApplication` in a sibling package (e.g., `com.acme.myapp`), not under `io.bugfab.adapter.*`. The auto-configuration class is still picked up via `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`, where `@ConditionalOnProperty` is honored correctly.
2. Explicitly exclude the adapter package from your scan:
   ```kotlin
   @SpringBootApplication(
       scanBasePackages = ["com.acme.myapp"],
       // or, equivalently:
       // exclude = [...]
   )
   class MyApplication
   ```
3. The bundled test suite uses workaround (1) — see `src/test/kotlin/io/bugfab/adapter/bootapp/` and `src/test/kotlin/io/bugfab/adapter/jpatest/`, both sibling packages of `io.bugfab.adapter`.

**v0.2 fix:** move the adapter's auto-config classes from `io.bugfab.adapter` to `io.bugfab.adapter.config`. Then no consumer scan rooted at `io.bugfab.adapter` can reach them as `@Configuration`s; they only activate through the auto-configuration imports file.

## DI scopes — singletons vs request-scoped state

The Python reference uses module-level singletons (`_STORAGE`, `_SETTINGS`, etc.) that consumers override via FastAPI's `app.dependency_overrides`. Spring's idiomatic equivalent is constructor-injected `@Bean`s. Every Bug-Fab bean — `Storage`, `BugFabRateLimiter`, `BugFabProperties` — is a default-scope singleton, suitable for one-time creation at startup.

If you need per-request state (e.g., a request-scoped `BugFabActor` so the lifecycle audit log records the current user), wire a `@RequestScope`-annotated component and have it expose the actor via a `@RequestAttribute` named `bug_fab_actor`. The controller reads this attribute, so a Spring Security filter that resolves the principal and sets the attribute is enough:

```kotlin
@Component
class BugFabActorFilter : OncePerRequestFilter() {
    override fun doFilterInternal(req: HttpServletRequest, res: HttpServletResponse, chain: FilterChain) {
        val principal = SecurityContextHolder.getContext().authentication?.name
        if (principal != null) req.setAttribute("bug_fab_actor", principal)
        chain.doFilter(req, res)
    }
}
```

## Profile activation

The adapter is profile-agnostic by design — consumers select storage via the `bugfab.storage` property, not via a `@Profile` annotation. This was deliberate: `@Profile`-gated beans would force consumers to wire `spring.profiles.active=jpa` just to swap backends, which collides with their own profile usage (`dev`, `prod`, `staging`).

If you want per-profile property files, use the standard Spring Boot convention:

```
src/main/resources/
  application.yml            # shared
  application-dev.yml        # active when SPRING_PROFILES_ACTIVE=dev
  application-prod.yml       # active when SPRING_PROFILES_ACTIVE=prod
```

Each profile file can override any `bugfab.*` property.

## JPA migrations — pick Flyway

Bug-Fab v0.1 ships no migration tooling. The JPA storage relies on Hibernate's `ddl-auto` for the schema, which is fine for tests (`create-drop`) and demo deployments (`update`) but absolutely not production.

**Recommendation: add Flyway in v0.2.** The adapter would ship `V1__bug_fab_init.sql` with the `bug_fab_reports` table DDL; consumers run their existing Flyway pipeline and pick up the migration automatically. Liquibase works equally well — pick whichever your team already uses.

Until v0.2 ships migrations, JPA users have three options:

1. **Hibernate auto-DDL (dev only):** `spring.jpa.hibernate.ddl-auto=update` — Hibernate adds missing columns at startup. Catastrophic in production (drops indexes silently on type changes).
2. **Hand-rolled SQL:** copy the DDL from the entity definition and run it via your normal database migration tool.
3. **Schema generation script:** `./gradlew :spring-boot-kotlin:jpaSchemaExport` (not implemented in v0.1, but trivial to add — see Hibernate's `SchemaExport`).

## Auto-configuration ordering vs Spring Security

The adapter's `@AutoConfiguration` is annotated `after = [HibernateJpaAutoConfiguration::class]` so JPA wiring lands first. Spring Security is a different story.

If a consumer's `SecurityFilterChain` uses method-level security (`@PreAuthorize` etc.), Spring Security's `MethodSecurityAutoConfiguration` runs at the same precedence as ours, and the order is non-deterministic across class-loader implementations. The symptom: a `@PreAuthorize` annotation on a custom storage bean appears to be silently ignored.

**Workaround:** if you wrap `Storage` with method-level auth, annotate your wrapper bean with `@DependsOn("methodSecurityInterceptor")` or move the auth check to the controller layer (where `RequestMatcher` rules apply normally).

## Rate limit at scale

The bundled `BugFabRateLimiter` uses a plain `ConcurrentHashMap<String, Bucket>`. Every distinct client IP allocates one bucket, and the map never shrinks. For a hobby deployment behind a single front-end this is fine — a million unique IPs is still a few-MB heap allocation.

For high-volume public deployments:

```kotlin
@Bean
fun bugFabRateLimiter(properties: BugFabProperties): BugFabRateLimiter {
    val proxyManager = Caffeine.newBuilder()
        .expireAfterAccess(Duration.ofMinutes(10))
        .build<String, Bucket>()
    // Wrap with BucketProxyManager and return a custom subclass.
    // ...
}
```

See Bucket4j's [`distributed`](https://bucket4j.com/8.10.1/toc.html#distributed-usage-scenarios) docs.

## MultipartFile gotcha — `getSize()` lies about disk-spooled parts

Spring's `MultipartFile.getSize()` returns the part's total byte count, regardless of whether the bytes are still on disk. Calling `.getBytes()` brings the whole part into JVM heap. The adapter does this only after the size cap fires, so an oversize upload is rejected before the bytes leave disk.

The hard cap from `spring.servlet.multipart.max-file-size` is enforced at the *parser* level, before any controller method runs. If a consumer raises `bugfab.max-screenshot-mb` past the parser's cap, the parser will return a 413 with Spring's stock plain-text body — not Bug-Fab's JSON envelope. Always set both properties together:

```yaml
bugfab:
  max-screenshot-mb: 8
spring:
  servlet:
    multipart:
      max-file-size: 8MB
      max-request-size: 9MB  # screenshot + metadata + multipart overhead
```

The exception handler maps `MaxUploadSizeExceededException` onto the right envelope, so the parser path is covered — but the redundant property is still required for the parser to allow the larger part in the first place.

## Reactive (WebFlux)

The adapter is Spring MVC only in v0.1. The controller's methods are synchronous (`fun submit(...): ResponseEntity<...>` instead of `suspend fun submit(...)`), which a WebFlux-only consumer cannot register.

WebFlux support is on the roadmap (v0.3 candidate). The protocol itself is HTTP-only and doesn't need backpressure, so a reactive port is mostly mechanical translation — but it's a non-trivial second module worth of work, not a flip-the-switch toggle.

## Kotlin compiler args

`build.gradle.kts` sets `-Xjsr305=strict` so JSR-305 nullability annotations on Java dependencies are honored at compile time. This catches the most common Spring-from-Kotlin foot-gun: `RequestParam("metadata") String metadata` is nullable on the Java side but compiles as non-null on Kotlin without the flag.

`-Xemit-jvm-type-annotations` is on for the JPA path — Hibernate validators (`@NotNull` etc.) need the annotations to survive into the class file's type-use slot, otherwise reflection-based discovery silently skips them.
