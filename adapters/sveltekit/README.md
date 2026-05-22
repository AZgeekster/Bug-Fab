# bug-fab-sveltekit

SvelteKit adapter for the [Bug-Fab](https://github.com/AZgeekster/Bug-Fab) wire
protocol (v0.1). Drop a few `+server.ts` files into your `src/routes/` tree
and ship a working bug-report intake + viewer in your SvelteKit app.

> Status: first-party reference adapter for the SvelteKit / Node ecosystem. Promoted from draft on 2026-05-21 after `npm test` was verified at 35 passed + 1 skipped (intake 8/8, conformance 15/15, viewer 12/13 + 1 skip) under `docker run --rm node:20`. npm publish pending tag. Tracks Bug-Fab v0.1. Tracked in the Bug-Fab adapters registry: <https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#sveltekit-typescript>.

## What this gives you

- `createIntakeHandler` — `POST /bug-reports` factory.
- `createListHandler`, `createDetailHandler`, `createScreenshotHandler` — read-side JSON viewer endpoints.
- `createStatusHandler`, `createDeleteHandler`, `createBulkCloseHandler`, `createBulkArchiveHandler` — write-side admin endpoints.
- `createViewerIndexHandler` — HTML index page for the viewer mount root (Adapter Authorship Checklist item 6).
- `FileStorage` — zero-dependency file-system backend for single-process Node deployments.
- `DrizzleStorage` — Drizzle ORM-backed backend for serverless / multi-worker / Postgres / SQLite / D1.
- `<BugFab />` Svelte component — type-safe wrapper around the upstream `window.BugFab.init(...)` browser bundle.

The package implements all 8 endpoints of the Bug-Fab wire protocol exactly
as specified in [`PROTOCOL.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md). JSON keys
are snake_case across the wire (no auto-camelCase).

## Install

```sh
pnpm add bug-fab-sveltekit
# or
npm install bug-fab-sveltekit
```

Peer dependencies (already in any SvelteKit project): `@sveltejs/kit ^2`.

## Quickstart

### 1. Wire up shared adapter options

```ts
// src/lib/server/bug-fab.ts
import { FileStorage } from 'bug-fab-sveltekit/server';
import type { BugFabAdapterOptions } from 'bug-fab-sveltekit/server';

export const storage = new FileStorage({
  storageDir: process.env.BUG_FAB_DIR ?? './var/bug-reports'
});

export const adapterOptions: BugFabAdapterOptions = {
  storage,
  resolveActor: (event) => {
    // pull the actor from your auth layer
    const locals = (event as { locals?: { user?: { email?: string } } }).locals;
    return locals?.user?.email ?? null;
  }
};
```

### 2. Add the route handlers

Each `+server.ts` is one line of wiring:

```ts
// src/routes/api/bug-reports/+server.ts
import { createIntakeHandler } from 'bug-fab-sveltekit/server';
import { adapterOptions } from '$lib/server/bug-fab';
export const POST = createIntakeHandler(adapterOptions);
```

Repeat for each of the 8 endpoints — see
[`examples/route-tree/`](./examples/route-tree/) for the full set.

### 3. Mount the FAB

```svelte
<!-- src/routes/+layout.svelte -->
<script lang="ts">
  import BugFab from 'bug-fab-sveltekit/client';
  import { page } from '$app/stores';
</script>

<slot />

<BugFab
  intakeEndpoint="/api/bug-reports"
  module={$page.route.id ?? undefined}
  appVersion="1.0.0"
  environment={import.meta.env.MODE}
/>
```

The component handles SSR by guarding all DOM access behind `onMount`.

## Deployment

### Adapter compatibility

Which SvelteKit deploy adapter you choose determines the runtime constraints.

| SvelteKit adapter | `FileStorage` works? | `DrizzleStorage` works? | Notes |
|-------------------|----------------------|-------------------------|-------|
| `adapter-node`    | yes                  | yes                     | Single-process: fine. Multi-process: use Drizzle. |
| `adapter-vercel`  | no (no fs)           | yes                     | Use Drizzle + Postgres / Neon. |
| `adapter-cloudflare` | no                | yes (D1 / libsql / Postgres) | Use Drizzle, store screenshots in R2. |
| `adapter-static`  | no (no server)       | no                      | Bug-Fab needs a server runtime. |
| Bun + `adapter-bun` | yes                | yes                     | Bun's fs APIs match Node. |

### Mounting the static browser bundle

The Bug-Fab browser bundle (`bug-fab.js`) ships **vendored inside this
package** at `node_modules/bug-fab-sveltekit/dist/static/bug-fab.js`. The
build pipeline copies it from `static/bug-fab.js` (vendored from the
upstream Bug-Fab monorepo by `pnpm run vendor:bundle`) into `dist/static/`
during `pnpm build`, and `dist/static/` is included in the published
tarball via the `files` array.

Copy it into your app's `static/` folder so SvelteKit serves it at
`/bug-fab/bug-fab.js`:

```js
// vite.config.ts
import { sveltekit } from '@sveltejs/kit/vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';

export default {
  plugins: [
    sveltekit(),
    viteStaticCopy({
      targets: [
        {
          src: 'node_modules/bug-fab-sveltekit/dist/static/bug-fab.js',
          dest: 'bug-fab'
        }
      ]
    })
  ]
};
```

Or copy manually:

```sh
mkdir -p static/bug-fab
cp node_modules/bug-fab-sveltekit/dist/static/bug-fab.js static/bug-fab/
```

#### For contributors building this package from source

The vendored `static/bug-fab.js` is the upstream Bug-Fab browser bundle
copied from `repo/static/bug-fab.js`. Consumers should replace it with the
latest upstream copy before deploying — `pnpm run vendor:bundle` does this
automatically from the in-repo source:

```sh
pnpm run vendor:bundle    # copies repo/static/bug-fab.js -> static/bug-fab.js
pnpm build                # produces dist/static/bug-fab.js for publishing
```

`prepublishOnly` chains `vendor:bundle` before `build`, so `npm publish`
always picks up the latest upstream bundle.

## Auth — mount-point delegation

Bug-Fab v0.1 ships no auth abstraction. Mount the intake under one URL prefix
(typically `/api/`) and the viewer under another (typically `/admin/`), then
use SvelteKit's `hooks.server.ts` to gate one but not the other. See
[`examples/route-tree/README.md`](./examples/route-tree/README.md) for the
full example.

## CSRF trade-off

SvelteKit has built-in CSRF protection that rejects cross-origin POST
requests with non-form Content-Types. The Bug-Fab intake POST is
`multipart/form-data` from the same origin, so it's accepted by default.

If your app accepts cross-origin submissions (e.g., a `tools.example.com`
admin app submitting bugs to `app.example.com`):

- SvelteKit doesn't have per-route CSRF toggles. Set
  `kit.csrf.checkOrigin: false` in `svelte.config.js` and apply your own
  CSRF check in `hooks.server.ts` so non-Bug-Fab routes stay protected.

## Conformance

This adapter ships a Docker-based cross-stack conformance harness at
[`conformance/`](./conformance/) that boots the canonical
`examples/route-tree/` consumer with `adapter-node` + `node build` and
runs the upstream Python conformance suite against it. One command:

```sh
cd conformance
./run-conformance.sh
```

**Status: 30/30 passing as of 2026-05-21.**

See [`conformance/README.md`](./conformance/README.md) for the full
breakdown — URL layout, boot quirks, why we use `build + node` instead
of `dev`.

You can also run the suite directly against any running adapter:

```sh
pip install --pre bug-fab
pytest --bug-fab-conformance \
  --base-url=http://localhost:5173/api \
  --viewer-base-url=http://localhost:5173/admin
```

The local `tests/conformance.test.ts` in this package is a TS-only
sanity probe; the Python suite at `conformance/` is the source of
truth for adapter conformance.

See [`CONFORMANCE.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/CONFORMANCE.md).

## Testing

```sh
pnpm test         # vitest run
pnpm typecheck    # tsc --noEmit
```

Coverage targets: ≥85% lines / ≥85% functions / ≥80% branches on `src/server/`.

## Roadmap

- v0.1.0 — first published release, FileStorage, Drizzle backend, conformance pass.
- v0.1.x — Cloudflare R2 / S3 screenshot adapters, rate-limit middleware.
- v0.2 — `AuthAdapter` integration once Bug-Fab v0.2 ships.

## License

MIT. See [LICENSE](./LICENSE).
