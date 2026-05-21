#!/usr/bin/env bash
#
# One-command cross-stack conformance check for the Bug-Fab Rust (Axum) adapter.
#
# Boots `bugfab-example` in a `rust:1.75` container, runs the Python
# bug-fab conformance plugin against it from a sibling `python:3.12-slim`
# container, and captures the result to `out/conformance-results.txt`.
#
# Usage:
#   ./run-conformance.sh
#
# Environment overrides:
#   KEEP_RUNNING          — set to "1" to skip teardown (poke-around debugging)
#   COMPOSE_PROJECT_NAME  — disambiguate parallel runs (default: bugfab-rust-conformance)
#
# Exit codes:
#   0   all conformance tests passed
#   non-zero  conformance failures or harness error (adapter never booted, etc.)
#
# WARNING: cold-cache `cargo build --release` of the rust-axum workspace can
# take 5-10 minutes inside the container. The healthcheck has a 7-minute
# start_period to accommodate; this script just waits for compose.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-bugfab-rust-conformance}"

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
  local status=$?
  if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
    echo
    echo "--- teardown ---"
    "${COMPOSE[@]}" -f docker-compose.yml down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

echo "--- boot rust-adapter (cold cargo build --release can take several minutes) ---"
# `--abort-on-container-exit` + `--exit-code-from tester` makes Compose
# terminate when the tester finishes and propagate its exit code, which is
# the pytest exit code (0 pass, non-zero fail).
"${COMPOSE[@]}" -f docker-compose.yml up \
  --abort-on-container-exit \
  --exit-code-from tester \
  rust-adapter tester
