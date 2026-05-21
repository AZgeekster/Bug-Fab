#!/usr/bin/env bash
# Bug-Fab Hono adapter conformance runner.
#
# 1. Boots the adapter under oven/bun:1 on port 8080.
# 2. Waits up to 30s for the server's healthcheck to pass.
# 3. Runs the bundled bug-fab conformance suite from a python:3.12 sibling
#    container against http://hono-server:8080.
# 4. Tears the stack down on exit regardless of the test outcome.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

cleanup() {
  docker compose down --remove-orphans --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[conformance] starting hono-server"
docker compose up -d --build hono-server

echo "[conformance] waiting up to 30s for hono-server healthcheck"
deadline=$(( $(date +%s) + 30 ))
while :; do
  status="$(docker inspect --format '{{.State.Health.Status}}' \
              "$(docker compose ps -q hono-server)" 2>/dev/null || echo "starting")"
  if [[ "$status" == "healthy" ]]; then
    echo "[conformance] hono-server healthy"
    break
  fi
  if (( $(date +%s) >= deadline )); then
    echo "[conformance] timeout waiting for hono-server (status=$status)" >&2
    echo "[conformance] last logs:" >&2
    docker compose logs hono-server >&2 || true
    exit 1
  fi
  sleep 1
done

echo "[conformance] running pytest suite"
docker compose run --rm conformance
exit_code=$?

echo "[conformance] exit code: $exit_code"
exit "$exit_code"
