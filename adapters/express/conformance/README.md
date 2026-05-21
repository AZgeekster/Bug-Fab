# Express adapter — cross-stack conformance harness

Boots the adapter's `examples/server.ts` in a Node 20 Docker container and
runs the upstream Bug-Fab Python pytest conformance suite against it from
a sibling Python 3.12 container. One command, pass/fail.

## Why this exists

The adapter's own test suite (`vitest`) hits the router in-process with
`supertest`. That proves the TypeScript is internally consistent, but
**doesn't** prove the wire protocol is byte-for-byte compatible with the
upstream Python plugin. This harness closes that gap — it runs the same
`pytest --bug-fab-conformance` checks every other Bug-Fab adapter has to
pass.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose`, not `docker-compose`).
  Tested with Docker 29.x.
- No host Python or Node install required — everything runs in containers.

## Run

```bash
./run-conformance.sh
```

The script exits with the pytest container's status: `0` for a passing
suite, non-zero for failures or boot problems. On Windows, run via
Git Bash or WSL.

Equivalent direct invocation (skips the teardown trap):

```bash
docker compose -f conformance/docker-compose.yml up \
    --build \
    --abort-on-container-exit \
    --exit-code-from conformance \
    conformance express-adapter
```

## How it works

1. **`express-adapter` service** — `node:20-bookworm-slim`, runs
   `npm ci && npx -y tsx@4 examples/server.ts` with `PORT=8080`. The
   adapter source is bind-mounted from the host so changes are immediate;
   `node_modules` lives in an anonymous volume so Linux doesn't try to
   reuse Windows-built binaries.
2. **Healthcheck** — Compose waits until
   `GET http://localhost:8080/admin/bug-reports/` (the HTML viewer root)
   returns a non-5xx response. The Node container is considered ready
   only once that succeeds.
3. **`conformance` service** — `python:3.12-slim`, mounts the Bug-Fab
   repo root and runs `pip install -e .[dev]` to install the local
   `bug_fab` package (and pytest). Then:

   ```bash
   pytest --bug-fab-conformance \
       --base-url=http://express-adapter:8080/admin/bug-reports
   ```

   The `--base-url` matches the mount path baked into `examples/server.ts`
   (`MOUNT_PATH = '/admin/bug-reports'`). The conformance plugin appends
   `/bug-reports`, `/reports`, `/bulk-close-fixed`, etc. to that prefix.

4. **Teardown** — `run-conformance.sh`'s `EXIT` trap calls
   `docker compose down --volumes` on the way out so the anonymous
   `node_modules` volume doesn't accumulate across runs. Set
   `KEEP_RUNNING=1` to skip teardown and poke the running server at
   <http://localhost:8080/admin/bug-reports/>.

## Files

| File                  | Purpose                                                      |
|-----------------------|--------------------------------------------------------------|
| `docker-compose.yml`  | Two-service stack: Node example server + Python pytest runner |
| `run-conformance.sh`  | One-shot wrapper with cleanup + exit-code propagation        |
| `README.md`           | This file                                                    |

The wiring lives entirely under this directory; no adapter sources are
modified by the conformance setup (per the
[wiring-only constraint](../README.md#testing)).

## Troubleshooting

- **`npm ci` is slow.** First run pulls the lockfile-resolved tree fresh;
  subsequent runs reuse the named `bugfab-conformance_adapter_node_modules`
  volume.
- **`pip install -e .` is slow.** Same story for the Python container —
  fastapi + jinja2 download once per fresh image.
- **`express-adapter` healthcheck times out.** Tail the logs:
  `docker compose logs express-adapter`. Common causes: a TypeScript
  syntax error in `examples/server.ts` or `src/`, or an upstream npm
  registry hiccup during `npm ci`.
- **Want to keep the server running after a failure?** Run with
  `KEEP_RUNNING=1 ./run-conformance.sh`, then `docker compose down` when
  you're done.

## See also

- [`bug_fab/conformance/README.md`](../../../bug_fab/conformance/README.md)
  — what the pytest plugin asserts and how it works.
- [`docs/CONFORMANCE.md`](../../../docs/CONFORMANCE.md) — upstream
  conformance methodology for all adapters.
- [`adapters/express/README.md`](../README.md) — adapter overview and
  in-process vitest suite.
