# AGENTS.md — bug-fab-sveltekit

Guidance for AI assistants integrating against this adapter inside a
SvelteKit consumer codebase.

## What this package is

A thin SvelteKit adapter for the Bug-Fab wire protocol (v0.1). It exposes
factory functions that return `RequestHandler`s, plus storage backends and a
Svelte component wrapping the upstream browser bundle.

The package does NOT:

- Define routes itself — consumers wire each endpoint into their own
  `src/routes/` tree using `+server.ts` files.
- Bundle the upstream browser JS — that bundle is a separate static asset
  (`bug-fab.js`) the consumer copies into their `static/` folder.
- Implement auth — Bug-Fab v0.1 uses mount-point delegation. Use
  `hooks.server.ts` for gating.

## Authoritative references — read FIRST

When generating Bug-Fab-related code, ground every decision in these:

1. [`PROTOCOL.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md) — wire spec.
2. [`protocol-schema.json`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json) — **authoritative**. Schema wins over prose.
3. [`ADAPTERS.md` § SvelteKit](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS.md#sveltekit) — reference notes.
4. [`ADAPTERS_REGISTRY.md` § Adapter authorship checklist](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#adapter-authorship-checklist) — 12-point conformance checklist.

## Common tasks

### "Add Bug-Fab to my SvelteKit app"

1. Install: `pnpm add bug-fab-sveltekit`.
2. Create `src/lib/server/bug-fab.ts` with a `FileStorage` (or Drizzle)
   instance and `BugFabAdapterOptions`.
3. For each of the 8 protocol endpoints, create a `+server.ts` file that
   imports the corresponding factory and exports it as the right HTTP method
   name. See [`examples/route-tree/`](./examples/route-tree/).
4. Add `<BugFab />` to `src/routes/+layout.svelte`.
5. Configure `vite-plugin-static-copy` to vendor `bug-fab.js` into `static/`.

### "Customize the actor identity in the lifecycle log"

Pass a `resolveActor` to `BugFabAdapterOptions`. The function receives the
SvelteKit `RequestEvent` (so you can read `event.locals.user`). Return a
string for the user identity, or `null` for anonymous.

### "Wire up GitHub Issues sync"

Set `github` in `BugFabAdapterOptions`:

```ts
github: {
  enabled: true,
  pat: env.GITHUB_PAT,
  repo: 'azgeekster/my-app',
  labels: ['bug', 'from-bug-fab']
}
```

GitHub sync is best-effort: failures log but never cause the intake response
to be non-2xx.

### "Switch storage backend for serverless"

Replace `FileStorage` with `DrizzleStorage` and pass in your Drizzle `db`
instance + table objects + a `screenshotIO` object that reads/writes
screenshots to wherever your runtime supports (R2, S3, BYTEA, etc.). See
[`src/server/storage/DrizzleStorage.ts`](./src/server/storage/DrizzleStorage.ts) for the reference schema.

## Anti-patterns (what NOT to do)

- **Do not camelCase JSON keys.** The wire format is snake_case. Drizzle's
  `.findFirst()` returns camelCase TypeScript objects — map them through
  the helpers in `DrizzleStorage.ts` before sending to the wire.
- **Do not silently coerce unknown enum values.** `severity: 'urgent'` must
  return 422 schema_error, not silently rewrite to "medium". The conformance
  suite explicitly tests for this.
- **Do not use `error()` from `@sveltejs/kit` for protocol errors.** It
  throws an `HttpError` whose default body is `{ message }`, which doesn't
  match the protocol's `{ error, detail }` envelope. Use `json({...}, {status})`
  via the `jsonError()` helper.
- **Do not reject deprecated enum values on read paths.** A list-filter or
  detail handler that 422s on a deprecated status will lock consumers out of
  historical data.
- **Do not trust `context.user_agent` as the source of truth.** Capture
  `request.headers.get('user-agent')` independently. Both values are stored;
  only the server-captured one is authoritative.

## Adapter compatibility cheatsheet

| Storage | Works on Node? | Vercel? | Cloudflare Workers? | Bun? |
|---------|----------------|---------|---------------------|------|
| FileStorage | yes (single process) | no | no | yes |
| DrizzleStorage + Postgres/Neon | yes | yes | yes (via fetch driver) | yes |
| DrizzleStorage + libsql/Turso | yes | yes | yes | yes |
| DrizzleStorage + D1 | n/a | n/a | yes | n/a |

## Conformance check

Always run the Python conformance suite against your live SvelteKit dev
server before shipping:

```sh
pnpm dev &
pytest --bug-fab-conformance --base-url=http://localhost:5173
```

The local `tests/conformance.test.ts` in this package is a TS-only sanity
check; the Python suite is the source of truth.

## Versioning

The package version is independent of the Bug-Fab protocol version it
implements. Read `peerDependencies` and the `Tracks Bug-Fab` field in the
public registry to know which protocol version the package targets.
