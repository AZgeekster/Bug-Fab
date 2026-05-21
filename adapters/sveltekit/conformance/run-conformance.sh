#!/usr/bin/env bash
# Run the cross-stack Bug-Fab conformance suite against the SvelteKit
# example consumer (examples/route-tree/), booted via adapter-node.
#
# What it does:
#   1. `docker compose up --build` boots the sveltekit-adapter service.
#      The container installs adapter deps, builds dist/, installs the
#      conformance app, runs `svelte-kit sync && vite build`, then
#      `node build` on :8080.
#   2. The compose-level healthcheck polls /api/bug-reports until the
#      SvelteKit server answers (allow up to ~3min on a cold image).
#   3. The conformance sibling container runs `pytest --bug-fab-conformance`
#      against the SvelteKit server.
#   4. We tear everything down on the way out, then exit with the pytest
#      container's status code.
#
# Usage:
#   ./run-conformance.sh
#
# Environment overrides:
#   COMPOSE_PROJECT_NAME  — disambiguate parallel runs
#                          (default: bugfab-sveltekit-conformance)
#   KEEP_RUNNING          — set to "1" to skip teardown (poke-around debugging)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-bugfab-sveltekit-conformance}"

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

echo "[run-conformance] building and starting sveltekit-adapter ..."
# `--abort-on-container-exit` makes Compose terminate when the
# conformance container finishes; `--exit-code-from conformance`
# propagates its status as this script's exit code.
docker compose up \
  --build \
  --abort-on-container-exit \
  --exit-code-from conformance \
  conformance sveltekit-adapter
