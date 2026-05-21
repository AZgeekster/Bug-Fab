# bug-fab-hono — cross-stack conformance

Runs the official Python `bug-fab` conformance suite against this adapter
under containers. Validates that the Hono adapter actually implements
[Bug-Fab v0.1](../../../docs/PROTOCOL.md) on the wire, not just that the
in-package vitest suite agrees with itself.

## Layout

| File | Purpose |
|------|---------|
| `boot.ts` | Bun entry-point that mounts the adapter on `:8080` with `MemoryStorage`. Imports straight from `../src/index.ts` so no build step is needed. |
| `docker-compose.yml` | Two services: `hono-server` (oven/bun:1) and `conformance` (python:3.12-slim). |
| `run-conformance.sh` | End-to-end runner: bring up, wait for healthcheck, invoke pytest, tear down. |

## Run

```bash
cd repo/adapters/hono/conformance
./run-conformance.sh
```

Exit code 0 means the suite passed. Any non-zero exit prints which
clauses failed.

The host-side port is `28080` (mapped to container `8080`) so the stack
does not collide with parallel conformance runs that hold `:8080` on the
host. Pytest itself talks to the server over the compose network as
`http://hono-server:8080`, so the host mapping is for manual debugging
only.

## Status

**29/30 passing as of 2026-05-21.**

Outstanding gap (tracked for v0.2):

- `test_intake.py::test_missing_screenshot_is_rejected` — when the
  multipart envelope is present but the `screenshot` part is missing,
  the adapter returns `415 unsupported_media_type` instead of the
  protocol-required `400`/`422`. The current handler classifies any
  non-conforming multipart as a content-type error before reaching
  per-part validation. Fix lives in `src/intake.ts` — needs to split
  "envelope is wrong" (415) from "envelope is fine but a required part
  is missing" (400/422).

## Why containers

The adapter targets four runtimes (Cloudflare Workers, Bun, Deno,
Vercel Edge) plus Node via `@hono/node-server`. Bun resolves
TypeScript without a build step, so `oven/bun:1` is the lowest-overhead
host for the conformance run. The Python sidecar avoids requiring
adapter contributors to install Python locally just to verify wire-
protocol conformance.

## Manual smoke test (without the runner)

```bash
docker compose up -d hono-server
curl -i http://localhost:28080/admin/bug-reports/   # 200 + HTML
docker compose down --remove-orphans --volumes
```
