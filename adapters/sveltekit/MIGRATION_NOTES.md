# Migration Notes — SvelteKit / Node specifics

Notes for adopters and future maintainers. Captures the trade-offs that aren't visible from reading the source alone.

## `+server.ts` route wiring

This adapter does NOT define its own routes — it exports factory functions that return SvelteKit `RequestHandler`s. Each of the 8 protocol endpoints is one tiny `+server.ts` file in your `src/routes/` tree. This keeps mount-point auth simple (you put `/api/...` next to your other API routes, `/admin/...` next to other admin routes) and avoids the cross-framework headache of a "mountable router" abstraction that SvelteKit doesn't have a first-class concept for.

The trade-off is one file per endpoint. The `examples/route-tree/` directory ships the full set verbatim — copy-paste, change the `adapterOptions` import path, done. If a future SvelteKit version ships a multi-method route shorthand, the factory functions already return the right shape to slot in.

## `FileStorage` vs `DrizzleStorage`

The package ships two `IStorage` implementations.

| Concern | FileStorage | DrizzleStorage |
| --- | --- | --- |
| Deps | `node:fs` only | `drizzle-orm` + a driver (pg / libsql / d1 / better-sqlite3) |
| Worker safety | Single-process Node only | Multi-worker, multi-host safe (DB does the locking) |
| Vercel / Cloudflare / serverless | No (no fs) | Yes (pair with Postgres / Neon / D1 / libsql / Turso) |
| Bun + `adapter-bun` | Yes (Bun's fs APIs match Node) | Yes |
| Screenshot storage | Local disk (`screenshots/`) | Pluggable via `screenshotIO` (R2, S3, BYTEA column, etc.) |
| Migration story | Nothing — first run creates the dir | Run `drizzle-kit push` against your DB |

Default in the Quickstart is `FileStorage` because the realistic SvelteKit deployment for a hobby / single-VM consumer is `adapter-node` running one process. The Drizzle backend is for `adapter-vercel`, `adapter-cloudflare`, multi-worker `adapter-node` behind PM2, and anyone who already has a Postgres in their stack.

Reports written with one backend are NOT readable by the other — the on-disk JSON layout and the DB row layout are different. Migration scripts between them are out of scope for v0.1.

## Server-only imports (`$lib/server/`)

`FileStorage` and `DrizzleStorage` are both server-only. Importing them from a Svelte component or a `+page.svelte` will fail at build time because SvelteKit's bundler refuses to ship `node:fs` to the browser.

The convention is to keep all adapter wiring in `$lib/server/` and re-export only the `<BugFab />` client component to your `+layout.svelte`. The package's `bug-fab-sveltekit/server` subpath export is the enforcement mechanism — anything under that subpath is server-only by construction.

## Svelte v4 → v5 peer-dep history

The first draft of this package pinned `svelte: "^4.2.12"` to mirror the SvelteKit 2.0.0 starter at the time. Between then and the promotion to first-party (2026-05-21), `@sveltejs/kit@2.60.1` started pulling in `@sveltejs/vite-plugin-svelte@^7`, which peer-requires `svelte ≥ 5.46`. The result: `npm install` against the original `package.json` produced an `ERESOLVE` peer-dep conflict that only resolved with `--legacy-peer-deps`.

The promotion pass relaxed the dev-dep pin to `svelte: "^5.0.0"`. The `peerDependencies` entry remains `svelte: "^4.0.0 || ^5.0.0"` so downstream consumers on either major can install cleanly. The component itself (`src/client/BugFab.svelte`) is intentionally written to the Svelte 4 / 5 overlap (no runes, no `$state`, no `$props()` — just a script block with exported props and an `onMount` guard) so it compiles under both compilers. If you're migrating a consumer from Svelte 4 to 5, the `<BugFab />` import keeps working through the bump.

## Vendoring the upstream browser bundle

The Bug-Fab browser bundle (`bug-fab.js`) is NOT bundled into the published tarball as a JS module — it ships as a static file at `static/bug-fab.js`, copied into `dist/static/` by `npm run build:static`, and surfaced to consumers via the `files` array in `package.json`.

Consumers vendor it into their app's `static/` folder (typically via `vite-plugin-static-copy`) so SvelteKit serves it at `/bug-fab/bug-fab.js`. The component's `bundleUrl` prop defaults to `/bug-fab/bug-fab.js`; override if you serve it from a different path.

`npm run vendor:bundle` copies the latest upstream `repo/static/bug-fab.js` into this package's `static/` folder. `prepublishOnly` chains `vendor:bundle` before `build`, so `npm publish` always picks up the latest upstream copy.

## CSRF and the intake endpoint

SvelteKit's built-in CSRF protection (`kit.csrf.checkOrigin`) rejects cross-origin POSTs with non-form Content-Types. The Bug-Fab intake POST is `multipart/form-data` from the same origin, so it's accepted by default — no extra config needed for the common case.

If your app accepts cross-origin submissions (a separate admin app posting bugs to your main app), SvelteKit doesn't have per-route CSRF toggles. Disable global checkOrigin and re-implement it in `hooks.server.ts` so non-Bug-Fab POST routes stay protected. The protocol's `Auth` line of defense is mount-point delegation, not CSRF.

## Future work

- `AuthAdapter` (v0.2) — currently `resolveActor` is a passthrough you wire from `event.locals.user`.
- Real GitHub Issues sync wiring (currently a stub returning `null` in the intake response).
- An R2 / S3 `screenshotIO` reference implementation in `examples/route-tree/`.
- Telemetry events through SvelteKit's logging hook.
