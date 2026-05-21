#!/usr/bin/env bash
#
# One-command cross-stack conformance check for the Bug-Fab Phoenix/Plug adapter.
#
# Boots `conformance/boot.exs` (a wrapper mirroring `examples/minimal/minimal.exs`
# on port 8080) in an elixir:1.16 container, runs the Python bug-fab
# conformance plugin against it from a sibling python:3.12-slim container, and
# captures the result to `out/conformance-results.txt`.
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

echo "--- boot phoenix-adapter ---"
if ! "${COMPOSE[@]}" -f docker-compose.yml up -d phoenix-adapter; then
  echo "ERROR: failed to start phoenix-adapter container" >&2
  exit 2
fi

echo "--- wait for /api/bug-reports to respond (up to 420s — first run pulls + compiles hex deps) ---"
deadline=$((SECONDS + 420))
healthy=0
while [ $SECONDS -lt $deadline ]; do
  state="$(docker inspect --format='{{.State.Health.Status}}' bug-fab-phoenix-conformance-adapter 2>/dev/null || echo "missing")"
  case "$state" in
    healthy)
      healthy=1
      break
      ;;
    unhealthy)
      echo "ERROR: phoenix-adapter container reported unhealthy" >&2
      break
      ;;
  esac
  sleep 3
done

if [ "$healthy" -ne 1 ]; then
  echo "--- phoenix-adapter logs (last 120 lines) ---" >&2
  docker logs bug-fab-phoenix-conformance-adapter 2>&1 | tail -120 >&2 || true
  exit 2
fi

echo "--- phoenix-adapter is up — last 5 lines of server logs ---"
docker logs bug-fab-phoenix-conformance-adapter 2>&1 | tail -5 || true
echo ""

echo "--- run pytest --bug-fab-conformance ---"
set +e
"${COMPOSE[@]}" -f docker-compose.yml run --rm runner
runner_exit=$?
set -e

echo ""
echo "--- conformance result: $( [ $runner_exit -eq 0 ] && echo PASS || echo FAIL ) (exit $runner_exit) ---"
echo "--- transcript: $HERE/out/conformance-results.txt ---"

exit $runner_exit
