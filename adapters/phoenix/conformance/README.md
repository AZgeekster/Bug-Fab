# Cross-stack conformance harness — Phoenix / Plug adapter

This directory wires the upstream `bug-fab-conformance` pytest suite to the
Elixir/Phoenix adapter's minimal example server, so a single command verifies
that the adapter honors the [Bug-Fab wire protocol](../../../docs/PROTOCOL.md)
end-to-end.

The harness is intentionally Docker-only — it runs the same way on a
maintainer's laptop, a CI runner, and a reviewer's box without any Elixir or
Python installed on the host. The conformance plugin and the adapter never
share a process; everything is HTTP, exactly as a real consumer would
integrate.

## Prerequisites

- Docker 24+ with the `docker compose` v2 plugin (or the legacy `docker-compose` binary).
- Roughly 900 MB of disk for the first `elixir:1.16` + `python:3.12-slim` pull, plus
  Hex deps (`plug`, `plug_cowboy`, `jason`, etc.) cached in named volumes after
  the first boot.

No Elixir toolchain. No Python toolchain. No `mix` or `pip` on the host.

## Run

From this directory:

```sh
./run-conformance.sh
```

What happens:

1. `docker compose up -d phoenix-adapter` runs an `elixir:1.16` container that:
   - Pulls Hex + rebar locally (`mix local.hex --force && mix local.rebar --force`).
   - Fetches the adapter's deps (`mix deps.get`).
   - Compiles the adapter (`mix compile`).
   - Boots `conformance/boot.exs`, which mirrors
     [`examples/minimal/minimal.exs`](../examples/minimal/minimal.exs) but binds
     `Plug.Cowboy` to port **8080** instead of 4000. See "Wrapper rationale"
     below for why we don't edit the example file.
2. A healthcheck waits up to 180 s for `GET /api/bug-reports` to return a
   non-5xx response. Any 2xx/4xx is acceptable — we only care that
   `Plug.Cowboy` bound the port and the router is dispatching.
3. A `python:3.12-slim` runner container `pip install`s the Bug-Fab package
   from the repo root, then runs:

   ```sh
   pytest --bug-fab-conformance \
          --base-url=http://phoenix-adapter:8080/api \
          --viewer-base-url=http://phoenix-adapter:8080/admin/bug-reports
   ```

   The split base URLs match the adapter's recommended mount pattern: intake
   is open under `/api`, viewer is auth-gated under `/admin/bug-reports`.
4. The full transcript lands in `./out/conformance-results.txt`. The runner
   exits with pytest's exit code: 0 if every protocol clause is honored,
   non-zero if any failed.
5. The harness tears down both containers on success or failure.

## Wrapper rationale (`boot.exs`)

`examples/minimal/minimal.exs` hard-codes `port: 4000` and exposes no env
override. Per the project rules, we treat the example file as read-only for
harness work and add a sibling wrapper (`conformance/boot.exs`) that:

- Reuses the same `BugFab.IntakeRouter` / `BugFab.ViewerRouter` mounts.
- Reads `PORT` (default `8080`) so the cross-stack harness can pick its own
  port without modifying the example a real consumer copies.
- Reads `BUG_FAB_STORAGE_DIR` so each run gets a clean throwaway dir.

If you want the boot logic to live in the example itself, raise it upstream
— this wrapper deliberately does not touch `lib/` or `examples/`.

## What passing looks like

A clean pass shows the pytest summary line near the bottom of the transcript:

```
================== N passed in X.XXs ==================
```

…and `run-conformance.sh` exits 0. When that is the case, update the
adapter's top-level `README.md` "Conformance" section to record the date and
the N/N tally.

## What a real failure looks like

```
================== M failed, K passed in X.XXs ==================
```

…and the exit code is 1. Each failing test's message names the protocol
clause it asserts (e.g. *"CC12: deprecated status 'resolved' MUST be rejected
on write"*). The protocol spec is the canonical authority — do **not** patch
the conformance suite to work around an adapter bug. If the adapter is
wrong, fix the adapter; if you are sure the suite misreads the spec, open an
upstream issue with the failing transcript and a curl reproduction.

## Re-running after a code change

`lib/` is mounted into the adapter container, so editing
`lib/bug_fab/*.ex` on the host and re-running the script picks the change up
on the next boot (a fresh `mix compile` runs). The runner reinstalls the
local `bug_fab` Python package each run, so changes there are picked up too.
No image rebuild required.

The `phoenix-deps` and `phoenix-build` named volumes persist between runs so
the second `mix deps.get` / `mix compile` finish in seconds.

## When to use plain `docker compose` directly

If you want to iterate without the script's teardown, you can:

```sh
docker compose -f docker-compose.yml up -d phoenix-adapter
docker compose -f docker-compose.yml run --rm runner
# ...iterate...
docker compose -f docker-compose.yml down --volumes
```

`run-conformance.sh` is just the documented one-shot path.
