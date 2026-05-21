#!/usr/bin/env bash
# Bug-Fab Spring Boot / Kotlin adapter — cross-stack conformance runner.
#
# Boots the example consumer via docker-compose, waits up to ~90s for the
# JVM to warm up, then runs the bundled `bug-fab-conformance` pytest plugin
# against the adapter's mount prefix.
#
# Exits non-zero if either the app fails to boot or any conformance test
# fails. On non-zero exit, the boot log is left in `./boot.log` for
# debugging.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PROJECT_NAME="bugfab-spring-conformance"
LOG_FILE="$HERE/boot.log"

cleanup() {
    # Always tear down — leftover containers on 8080 break the next run.
    docker compose -p "$PROJECT_NAME" down --remove-orphans --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Starting Spring Boot adapter (gradle:8-jdk17, JVM warmup is slow)..."
# Capture the boot log to a file so we can show it on failure without
# tangling stdout with the pytest output below.
docker compose -p "$PROJECT_NAME" up --abort-on-container-exit \
    --exit-code-from tester 2>&1 | tee "$LOG_FILE"

EXIT_CODE="${PIPESTATUS[0]}"

if [ "$EXIT_CODE" -ne 0 ]; then
    echo ""
    echo "==> Conformance run failed (exit $EXIT_CODE). See $LOG_FILE for details."
    exit "$EXIT_CODE"
fi

echo ""
echo "==> Conformance run succeeded."
