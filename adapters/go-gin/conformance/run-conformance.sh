#!/usr/bin/env bash
#
# One-command cross-stack conformance check for the Bug-Fab Go (Gin) adapter.
#
# Boots `examples/minimal/main.go` in a golang:1.23 container, runs the Python
# bug-fab conformance plugin against it from a sibling python:3.12 container,
# and captures the result to `conformance-results.txt`.
#
# Usage:
#   ./run-conformance.sh
#
# Exit codes:
#   0   all conformance tests passed
#   1   one or more conformance tests failed (adapter is non-conformant)
#   2   harness failure (adapter never booted, docker error, etc.)
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

mkdir -p ./out
: > ./out/conformance-results.txt

# Use the v2 plugin (`docker compose`) and fall back to the v1 binary
# (`docker-compose`) for older hosts. The flags are identical for our subset.
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: docker compose / docker-compose not available" >&2
  exit 2
fi

cleanup() {
  echo "--- teardown ---"
  "${COMPOSE[@]}" -f docker-compose.yml down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- boot adapter ---"
# `up -d adapter` starts only the Go server. The runner waits for the
# adapter's healthcheck via depends_on.condition: service_healthy.
if ! "${COMPOSE[@]}" -f docker-compose.yml up -d adapter; then
  echo "ERROR: failed to start adapter container" >&2
  exit 2
fi

echo "--- wait for /api/bug-fab/reports to respond (up to 60s) ---"
# Compose's healthcheck handles this for the runner's depends_on, but we
# also poll here so we can fail fast with a clean error message rather than
# letting the runner spin up only to discover a dead adapter.
deadline=$((SECONDS + 60))
healthy=0
while [ $SECONDS -lt $deadline ]; do
  state="$(docker inspect --format='{{.State.Health.Status}}' bug-fab-go-conformance-adapter 2>/dev/null || echo "missing")"
  case "$state" in
    healthy)
      healthy=1
      break
      ;;
    unhealthy)
      echo "ERROR: adapter container reported unhealthy" >&2
      break
      ;;
  esac
  sleep 2
done

if [ "$healthy" -ne 1 ]; then
  echo "--- adapter logs (first 80 lines) ---" >&2
  docker logs bug-fab-go-conformance-adapter 2>&1 | head -80 >&2 || true
  exit 2
fi

echo "--- adapter is up — first 5 lines of server logs ---"
docker logs bug-fab-go-conformance-adapter 2>&1 | head -5 || true
echo ""

echo "--- run pytest --bug-fab-conformance ---"
# Run the runner service in the foreground. Exit code maps directly to
# pytest's exit code: 0 = all pass, non-zero = at least one failure.
set +e
"${COMPOSE[@]}" -f docker-compose.yml run --rm runner
runner_exit=$?
set -e

echo ""
echo "--- conformance result: $( [ $runner_exit -eq 0 ] && echo PASS || echo FAIL ) (exit $runner_exit) ---"
echo "--- transcript: $HERE/out/conformance-results.txt ---"

exit $runner_exit
