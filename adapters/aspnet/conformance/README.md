# Cross-stack conformance harness — ASP.NET Core adapter

This directory wires the upstream `bug-fab-conformance` pytest suite to the
ASP.NET adapter's example server, so a single command verifies that the adapter
honors the [Bug-Fab wire protocol](../../../docs/PROTOCOL.md) end-to-end.

The harness is intentionally Docker-only — it runs the same way on a
maintainer's laptop, a CI runner, and a reviewer's box without any .NET or
Python installed on the host. The conformance plugin and the adapter never
share a process; everything is HTTP, exactly as a real consumer would
integrate.

## Prerequisites

- Docker 24+ with the `docker compose` v2 plugin (or the legacy `docker-compose` binary).
- Roughly 1.2 GB of disk for the first `dotnet/sdk:8.0` + `python:3.12` pull.

No .NET SDK. No Python toolchain. No `pip install` on the host.

## Run

From this directory:

```sh
./run-conformance.sh
```

What happens:

1. `docker compose up -d adapter` copies the adapter source into the container
   (excluding host `bin/`/`obj/` so stale artifacts never leak into the Linux
   build) and runs `dotnet run --project examples/MinimalApi` inside a
   `dotnet/sdk:8.0` container. The first run restores NuGet packages into a
   named volume — expect a few minutes; re-runs are fast.
2. A healthcheck waits up to ~5 min for Kestrel to accept on :8080 (Kestrel
   only accepts once every route is mapped).
3. A `python:3.12` runner container `pip install`s the Bug-Fab package from the
   repo root, then runs:

   ```sh
   pytest --bug-fab-conformance \
          --base-url=http://adapter:8080/bug-fab \
          --viewer-base-url=http://adapter:8080/bug-fab
   ```

4. The full transcript lands in `./out/conformance-results.txt`. The runner
   exits with pytest's exit code: 0 if every protocol clause is honored,
   non-zero if any failed.
5. The harness tears down both containers on success or failure.

## What passing looks like

A clean pass shows the pytest summary line near the bottom of the transcript:

```
================== N passed in X.XXs ==================
```

…and `run-conformance.sh` exits 0. When that is the case, update the adapter's
top-level `README.md` "Conformance" note to record the date and the N/N tally.

## What a real failure looks like

```
================== M failed, K passed in X.XXs ==================
```

…and the exit code is 1. Each failing test's message names the protocol clause
it asserts. The protocol spec is the canonical authority — do **not** patch the
conformance suite to work around an adapter bug. If the adapter is wrong, fix
the adapter; if you are sure the suite misreads the spec, open an upstream
issue with the failing transcript and a curl reproduction.

## Re-running after a code change

The adapter source is copied into the container at boot, so editing the C#
source on the host and re-running the script picks the change up on the next
boot. The runner container reinstalls the local `bug_fab` package each run, so
changes there are picked up too. No image rebuild required.

## When to use plain `docker compose` directly

If you want to iterate without the script's teardown, you can:

```sh
docker compose -f docker-compose.yml up -d adapter
docker compose -f docker-compose.yml run --rm runner
# ...iterate...
docker compose -f docker-compose.yml down --volumes
```

`run-conformance.sh` is just the documented one-shot path.
