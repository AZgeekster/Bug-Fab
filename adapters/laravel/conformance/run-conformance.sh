#!/usr/bin/env bash
# Run the cross-stack Bug-Fab conformance suite against a minimal Laravel
# host that consumes the local adapter via a Composer `path` repository.
#
# What it does:
#   1. `docker compose up --build` boots the laravel-adapter service:
#      installs composer + PHP extensions, runs `composer create-project
#      laravel/laravel`, requires the local adapter via a `path` repo,
#      runs `php artisan migrate`, and exec's `php artisan serve` on :8080.
#   2. The compose-level healthcheck waits until the PHP server answers
#      a request on /api/bug-reports (a bare GET returns 405 — that
#      counts as "up").
#   3. The conformance sibling container runs `pytest --bug-fab-conformance`
#      against http://laravel-adapter:8080/api (intake) and
#      http://laravel-adapter:8080/admin/bug-reports (viewer).
#   4. We tear everything down on the way out, then exit with the pytest
#      container's status code.
#
# Usage:
#   ./run-conformance.sh
#
# Environment overrides:
#   COMPOSE_PROJECT_NAME  — disambiguate parallel runs (default: bugfab-conformance-laravel)
#   KEEP_RUNNING          — set to "1" to skip teardown (for poke-around debugging)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-bugfab-conformance-laravel}"

cleanup() {
  local status=$?
  if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
    echo
    echo "[run-conformance] tearing down ..."
    docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

echo "[run-conformance] building and starting laravel-adapter ..."
docker compose up \
  --build \
  --abort-on-container-exit \
  --exit-code-from conformance \
  conformance laravel-adapter
