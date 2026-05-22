# bug-fab-sveltekit — cross-stack conformance

Runs the official Python `bug-fab` conformance suite against this adapter
under containers. Validates that the SvelteKit adapter actually
implements [Bug-Fab v0.1](../../../docs/PROTOCOL.md) on the wire, not
just that the in-package vitest suite agrees with itself.

## Layout

| File | Purpose |
|------|---------|
| `package.json` | Minimal SvelteKit app: depends on the adapter via `file:..` plus `@sveltejs/adapter-node` for the canonical Node deploy target. |
| `svelte.config.js` | Wires `adapter-node`; disables CSRF check-origin so the python sidecar can POST cross-host into the intake endpoint. |
| `vite.config.ts` | One-line `sveltekit()` plugin invocation. |
| `tsconfig.json` | Inherits the auto-generated `.svelte-kit/tsconfig.json`. |
| `src/app.html` / `src/app.d.ts` | The mandatory SvelteKit app shell — kept intentionally empty. |
| `docker-compose.yml` | Two services: `sveltekit-adapter` (node:20-bookworm-slim) and `conformance` (python:3.12-slim). |
| `run-conformance.sh` | End-to-end runner: bring up, wait for healthcheck, invoke pytest, tear down. |

`src/routes/` and `src/lib/` are populated at boot from
`../examples/route-tree/src/` — the canonical consumer layout that the
adapter README points contributors at. They are listed in `.gitignore`
because they are derived, not source.

## Boot mode

`build + node`, not `dev`. The adapter container does:

```
cd /adapter && npm install && tsc --noCheck && npm run build:static
cd /conformance && npm install && npm install /adapter
                 && cp -r /adapter/examples/route-tree/src/routes src/routes
                 && cp -r /adapter/examples/route-tree/src/lib src/lib
                 && npm run build           # svelte-kit sync + vite build
                 && node build              # the production-mode launch
```

`node build` is the canonical production launch produced by
`adapter-node` — same code path real consumers run. `vite dev` would add
HMR + module-graph behaviors that mask the bugs the conformance suite
is meant to catch.

## Run

```bash
cd repo/adapters/sveltekit/conformance
./run-conformance.sh
```

Exit code 0 means the suite passed. Cold runs take ~3-5 minutes
(npm install of SvelteKit + adapter-node tree, then `vite build`); warm
runs (named volumes preserved) are noticeably faster.

## URL layout

The `examples/route-tree/src/routes/` tree mounts the protocol's eight
endpoints under SvelteKit-idiomatic prefixes:

| Endpoint | URL |
|----------|-----|
| Intake (POST)                       | `/api/bug-reports` |
| List (GET)                          | `/admin/reports` |
| Detail (GET) / Delete (DELETE)      | `/admin/reports/[id]` |
| Status (PUT)                        | `/admin/reports/[id]/status` |
| Screenshot (GET)                    | `/admin/reports/[id]/screenshot` |
| Bulk close-fixed (POST)             | `/admin/bulk-close-fixed` |
| Bulk archive-closed (POST)          | `/admin/bulk-archive-closed` |

Bulk endpoints sit as siblings of `/reports` (not under it) to match
the wire protocol's `/{viewer-base}/bulk-*` shape — `+server.ts` files
live at `src/routes/admin/bulk-*/`, not under `src/routes/admin/reports/`.

So the conformance command becomes:

```
pytest --bug-fab-conformance \
  --base-url=http://sveltekit-adapter:8080/api \
  --viewer-base-url=http://sveltekit-adapter:8080/admin
```

## Status

**30/30 passing as of 2026-05-21** (after porting Hono's intake CT-classification
fix and moving the bulk routes out from under `/admin/reports/`).

## Why a full SvelteKit app

Hono's conformance can boot a single `boot.ts` because Bun resolves TS
natively and the adapter is a pure framework module. SvelteKit's
runtime is a build artifact — route handlers are discovered from the
file tree, then bundled by Vite, then served by `adapter-node`'s
generated server. Skipping any of these would skip exactly the code
paths real consumers run.

## Quirks documented for future maintainers

- **`--legacy-peer-deps` IS needed.** SvelteKit 2.60.x's transitive
  `@sveltejs/vite-plugin-svelte@^7` peer-requires `svelte ≥ 5.46`,
  which intersects awkwardly with our `^5.0.0` dev pin under npm@10.
  `--legacy-peer-deps` is a no-op when resolution is clean and an
  escape hatch when it isn't. Mirrors the rationale in
  `../MIGRATION_NOTES.md`.
- **`vite` installed explicitly.** `svelte-package` (in the adapter's
  build) and `vite build` (in the conformance app's build) need vite
  on disk. The adapter doesn't list it as a dev-dep because
  contributors usually have it from another source; we add it
  explicitly here on a clean Linux image.
- **Adapter's `npm run build` is bypassed.** The adapter's
  `svelte.config.js` imports `vitePreprocess` from `@sveltejs/kit/vite`
  (the old export path) which fails under SvelteKit 2.60.x. We skip
  the full library build and run `tsc --noCheck` + `npm run
  build:static` directly to produce the `dist/server/` tree the
  example routes import. Conformance only exercises server code so
  the Svelte client component is irrelevant here.
- **`file:..` becomes an absolute install.** `npm install file:..` in
  `package.json` produces a `node_modules/bug-fab-sveltekit -> ../..`
  symlink whose target resolves to `/conformance/` inside the
  container (not `/adapter/`, because `/conformance` is the bind-mount
  root, not a subdirectory of `/adapter`). The boot script reinstalls
  with `npm install /adapter` to overwrite that symlink with one that
  points where it should.
- **`build/` and `.svelte-kit/` are NOT named volumes.**
  `adapter-node` calls `rmSync('build')` before writing, which fails
  on a mount point with `EBUSY`. They live under the bind-mounted
  `/conformance` and are gitignored.

## Manual smoke test (without the runner)

```bash
docker compose up -d sveltekit-adapter
# Wait ~5 min on a cold cache, then:
docker run --rm --network bugfab-sveltekit-conformance_default \
  curlimages/curl:latest curl -sf http://sveltekit-adapter:8080/admin/reports | jq .
docker compose down --remove-orphans --volumes
```
