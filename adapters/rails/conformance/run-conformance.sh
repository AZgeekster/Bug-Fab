#!/usr/bin/env bash
#
# One-command cross-stack conformance check for the Bug-Fab Rails adapter.
#
# Boots the in-repo `test/dummy/` app in a `ruby:3.3` container via
# conformance_boot.rb, runs the Python bug-fab conformance plugin against it
# from a sibling `python:3.12-slim` container, and captures the result to
# `./out/conformance-results.txt`.
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

echo "--- boot rails-adapter (this includes bundle install on first run) ---"
# `up -d rails-adapter` starts only the Rails server. The runner waits for
# the healthcheck via depends_on.condition: service_healthy.
if ! "${COMPOSE[@]}" -f docker-compose.yml up -d rails-adapter; then
  echo "ERROR: failed to start rails-adapter container" >&2
  exit 2
fi

echo "--- wait for /bug-fab/bug-reports to respond (up to 180s) ---"
# Generous timeout — first boot does `bundle install` from scratch.
deadline=$((SECONDS + 180))
healthy=0
while [ $SECONDS -lt $deadline ]; do
  state="$(docker inspect --format='{{.State.Health.Status}}' bug-fab-rails-conformance-adapter 2>/dev/null || echo "missing")"
  case "$state" in
    healthy)
      healthy=1
      break
      ;;
    unhealthy)
      echo "ERROR: rails-adapter container reported unhealthy" >&2
      break
      ;;
  esac
  sleep 3
done

if [ "$healthy" -ne 1 ]; then
  echo "--- rails-adapter logs (last 120 lines) ---" >&2
  docker logs --tail 120 bug-fab-rails-conformance-adapter >&2 || true
  exit 2
fi

echo "--- rails-adapter is up — first 10 lines of server logs ---"
docker logs bug-fab-rails-conformance-adapter 2>&1 | head -10 || true
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
