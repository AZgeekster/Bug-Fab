# Spring Boot / Kotlin adapter — cross-stack conformance

This directory wires the bundled [`bug-fab-conformance`](../../../bug_fab/conformance/README.md)
pytest plugin against the Spring Boot adapter's `examples:minimal` consumer.

It exists so a single command — `./run-conformance.sh` — can prove the
adapter is wire-protocol compatible against an unmodified copy of the
canonical conformance suite, with no JVM, Gradle, or Python required on
the host machine (just Docker).

## Run

```bash
cd repo/adapters/spring-boot-kotlin/conformance
./run-conformance.sh
```

The script:

1. Boots `examples:minimal` inside `gradle:8-jdk17` via `docker compose`.
2. Waits up to 90s for the embedded Tomcat to bind on `8080` (a TCP
   probe — JVM warmup plus Gradle's first-run Maven Central downloads
   typically clear in 60–75s on a cold cache, 10–20s warm).
3. Runs `pytest --bug-fab-conformance --base-url=http://app:8080/bug-fab`
   inside a `python:3.12-slim` sibling that installs `bug-fab` from PyPI.
4. Tears down on exit (always — including failure), and leaves the full
   boot + test log in `./boot.log` for debugging.

Exit code is the conformance suite's exit code (0 = all green).

## Boot-time warning

**Spring Boot is the slowest of the four cross-stack conformance
targets.** Cold-cache cycle time is dominated by Gradle resolving Spring
Boot + Kotlin + Bucket4j against Maven Central on first run, plus JVM
warmup. Expect:

| Run                  | Wall time   |
|----------------------|-------------|
| First run, cold cache | ~3–5 min   |
| Subsequent runs       | ~30–60s    |

The Gradle cache is preserved in a named Docker volume
(`bugfab_spring_gradle_cache`), so the second-run time is what you'll
see in CI once the cache is warm. The health check inside
`docker-compose.yml` is set to 18 retries × 5s = 90s of patience after
the container starts, which is enough for warm boots and tight for cold
ones — bump `retries:` if your first run on a slow link races the
healthcheck.

## How the prefix maps

The adapter's default `bugfab.route-prefix` is `/bug-fab`. Intake and
viewer share that prefix in the minimal example, so the conformance
suite is run with `--base-url=http://app:8080/bug-fab` and
`--viewer-base-url` defaults to the same value. Split-prefix
deployments (intake open, viewer auth-gated) would override
`--viewer-base-url` separately; the minimal example doesn't.

## What's tested

Whatever the bundled `bug_fab.conformance` suite covers — see
[`../../bug_fab/conformance/README.md`](../../../bug_fab/conformance/README.md)
for the up-to-date module list. As of this writing: intake happy/sad
paths, viewer pagination + filters + 404s, status workflow + bulk
operations, deprecated `status: "resolved"` rejection, and
`environment` round-trip.

## CI

This is intentionally a shell + docker-compose harness, not a Gradle
task — keeping the conformance step language-agnostic means the same
script can run from a GitHub Actions matrix job that also exercises
the other adapters' conformance suites side-by-side.

Mount it into a job step with:

```yaml
- name: Spring Boot adapter conformance
  run: ./repo/adapters/spring-boot-kotlin/conformance/run-conformance.sh
```

No JDK, Kotlin, or Python install needed on the runner — Docker is the
only prerequisite.
