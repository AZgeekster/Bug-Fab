// Bug-Fab Spring Boot adapter — Gradle Kotlin DSL build script.
//
// Targets Spring Boot 3.x on Java 17 / Kotlin 1.9+ with two storage
// backends (file and JPA). The intake endpoint is the security-critical
// path: we explicitly stream the multipart screenshot rather than
// buffering the whole part with `@RequestPart` (see the controller for
// the rationale).

plugins {
    kotlin("jvm") version "1.9.25"
    kotlin("plugin.spring") version "1.9.25"
    kotlin("plugin.jpa") version "1.9.25"
    id("org.springframework.boot") version "3.3.5" apply false
    id("io.spring.dependency-management") version "1.1.6"
    `java-library`
    `maven-publish`
}

group = "io.bugfab"
version = "0.1.0-SNAPSHOT"

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
}

allprojects {
    repositories {
        mavenCentral()
    }
}

dependencyManagement {
    imports {
        mavenBom(org.springframework.boot.gradle.plugin.SpringBootPlugin.BOM_COORDINATES)
    }
}

dependencies {
    // Spring Boot stack — declared as `api` so consumers pulling in the
    // adapter automatically get Web + Validation on their classpath.
    api("org.springframework.boot:spring-boot-starter-web")
    api("org.springframework.boot:spring-boot-starter-validation")

    // JPA is `implementation` because not every consumer wants it —
    // they can opt into JpaStorage via the `bugfab.storage=jpa` property
    // and add their own database driver.
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin")
    implementation("org.jetbrains.kotlin:kotlin-reflect")

    // Bucket4j ships an optional Spring Boot starter, but the dependency
    // graph is heavy. We use the core library and wire a simple per-IP
    // bucket cache ourselves — keeps the adapter footprint small and
    // avoids forcing consumers to opt into Hazelcast / Caffeine just to
    // get rate limiting.
    implementation("com.bucket4j:bucket4j-core:8.10.1")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("com.h2database:h2")
    testImplementation("org.jetbrains.kotlin:kotlin-test-junit5")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

kotlin {
    compilerOptions {
        freeCompilerArgs.addAll(
            "-Xjsr305=strict",
            "-Xemit-jvm-type-annotations",
        )
        // JPA entities need an open class + no-arg constructor; the
        // kotlin("plugin.jpa") plugin handles `@Entity` automatically,
        // and `plugin.spring` opens classes marked with Spring stereotype
        // annotations. Hand-listing them here would be redundant.
    }
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = false
    }
}

publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            from(components["java"])
            pom {
                name.set("Bug-Fab Spring Boot adapter")
                description.set(
                    "Spring Boot 3.x adapter for the Bug-Fab wire protocol — " +
                        "intake + viewer endpoints, file or JPA storage."
                )
                url.set("https://github.com/AZgeekster/Bug-Fab")
                licenses {
                    license {
                        name.set("MIT")
                        url.set("https://opensource.org/license/mit")
                    }
                }
            }
        }
    }
}
