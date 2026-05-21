# Cross-stack conformance harness — Rust (Axum) adapter

This directory wires the upstream `bug-fab-conformance` pytest suite to the
Rust adapter's `bugfab-example` binary, so a single command verifies that the
Rust adapter honors the [Bug-Fab wire protocol](../../../docs/PROTOCOL.md)
end-to-end.

The harness is intentionally Docker-only — it runs the same way on a
maintainer's laptop, a CI runner, and a reviewer's box without any Rust or
Python installed on the host. The conformance plugin and the adapter never
share a process; everything is HTTP, exactly as a real consumer would
integrate.

## Prerequisites

- Docker 24+ with the `docker compose` v2 plugin (or the legacy
  `docker-compose` binary).
- Roughly 2 GB of disk for the first `rust:1.75` + `python:3.12-slim` pull
  plus the cargo registry / target caches.
- Patience on the first run — cold-cache `cargo build --release` of the
  workspace is **5-10 minutes** inside the container.

No Rust toolchain. No Python toolchain. No `cargo`/`pip` on the host.

## Run

From this directory:

```sh
./run-conformance.sh
```

What happens:

1. `docker compose up rust-adapter tester` starts the example server
   (`cargo run --release -p bugfab-example`) in a `rust:1.75` container with
   the repo root mounted read-only. The cargo registry and `target/` go to
   named volumes so subsequent runs hit the warm cache and boot in seconds.
2. A healthcheck waits for `GET http://127.0.0.1:8080/reports` to return a
   non-5xx response, with a 7-minute `start_period` to cover the cold build.
3. The tester container (`python:3.12-slim`) shares the adapter's network
   namespace, `pip install`s the local Bug-Fab Python package, then runs:

   ```sh
   pytest --bug-fab-conformance \
          --base-url=http://127.0.0.1:8080 \
          --viewer-base-url=http://127.0.0.1:8080 \
          --rootdir=/work
   ```

4. The full transcript lands in `./out/conformance-results.txt`. Compose's
   `--exit-code-from tester` propagates pytest's exit code — 0 if every
   protocol clause passes, non-zero on any failure.
5. The script's `EXIT` trap tears down both containers (set `KEEP_RUNNING=1`
   to skip teardown for debugging).

## URL plumbing

The example uses `bugfab::build_app(state)` which `.merge()`s `intake_router`
and `viewer_router` at the **root** of the Axum app — no `/api` prefix and no
`/admin` prefix. So:

| Conformance plugin asks for | Actual path on the example server |
|-----------------------------|-----------------------------------|
| `POST {base-url}/bug-reports` | `POST /bug-reports` |
| `GET {viewer-base-url}/reports` | `GET /reports` |
| `PUT {viewer-base-url}/reports/{id}/status` | `PUT /reports/{id}/status` |
| `POST {viewer-base-url}/bulk-close-fixed` | `POST /bulk-close-fixed` |

Both `--base-url` and `--viewer-base-url` therefore point at the same naked
host (`http://127.0.0.1:8080`). A production consumer mounting intake and
viewer separately would split them; the example's combined router is for
POCs and conformance.

## Why the tester shares the adapter's network namespace

`bugfab-example/src/main.rs` binds the listener to `127.0.0.1:8080` (not
`0.0.0.0`). On a normal user-defined Compose bridge the tester would not be
able to reach the adapter's loopback. We can't change the adapter source
under this harness's scope, so the tester uses
`network_mode: "service:rust-adapter"` to share the adapter container's
network namespace — `127.0.0.1:8080` then resolves to the listener directly.

## What passing looks like

A clean pass shows the pytest summary line near the bottom of the transcript:

```
================== N passed in X.XXs ==================
```

…and `run-conformance.sh` exits 0. When that is the case, update the
adapter's top-level `README.md` "Conformance" row to record the date and the
N/N tally.

## What a real failure looks like

```
================== M failed, K passed in X.XXs ==================
```

…and the exit code is 1. Each failing test's message names the protocol
clause it asserts (e.g. *"intake response shape: `report_id` MUST be the
top-level key, not nested under `data`"*). The protocol spec is the
canonical authority — do **not** patch the conformance suite to work around
an adapter bug. If the adapter is wrong, fix the adapter; if the suite
misreads the spec, open an upstream issue with the failing transcript.

## Re-running after a code change

The Rust source is mounted read-only into the adapter container, but the
build outputs go to a named `cargo-target` volume — so cargo's incremental
build will pick up host-side edits to `bugfab/*.rs` or `bugfab-example/*.rs`
without an image rebuild. The tester container reinstalls the local
`bug_fab` Python package each run, so changes there are picked up too.

## When to use plain `docker compose` directly

For iteration without the script's teardown:

```sh
docker compose -f docker-compose.yml up -d rust-adapter
docker compose -f docker-compose.yml run --rm tester
# ...iterate...
docker compose -f docker-compose.yml down --volumes
```

`run-conformance.sh` is just the documented one-shot path.
