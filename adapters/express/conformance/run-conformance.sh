#!/usr/bin/env bash
# Run the cross-stack Bug-Fab conformance suite against the Express
# example server.
#
# What it does:
#   1. `docker compose up --build` boots the express-adapter service.
#   2. The compose-level healthcheck waits until the Node server answers
#      a request on /admin/bug-reports/ (up to ~60s).
#   3. The conformance sibling container runs `pytest --bug-fab-conformance`
#      against http://express-adapter:8080/admin/bug-reports.
#   4. We tear everything down on the way out, then exit with the pytest
#      container's status code.
#
# Usage:
#   ./run-conformance.sh
#
# Environment overrides:
#   COMPOSE_PROJECT_NAME  — disambiguate parallel runs (default: bugfab-conformance)
#   KEEP_RUNNING          — set to "1" to skip teardown (for poke-around debugging)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-bugfab-conformance}"

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

echo "[run-conformance] building and starting express-adapter ..."
# `--abort-on-container-exit conformance` makes Compose terminate when the
# conformance container finishes, propagating its exit code via `--exit-code-from`.
docker compose up \
  --build \
  --abort-on-container-exit \
  --exit-code-from conformance \
  conformance express-adapter
