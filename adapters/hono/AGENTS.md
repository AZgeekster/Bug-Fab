# AGENTS.md — bug-fab-hono

Notes for AI assistants working on this package. Humans: see `README.md` first.

## What this package is

Hono adapter for the Bug-Fab v0.1 wire protocol. Edge-runtime-friendly: runs on Cloudflare Workers, Bun, Deno, Vercel Edge, and Node.

The wire protocol is the contract — see:

- `https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md` — prose
- `https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json` — **authoritative** JSON Schema (wins on disagreement)
- `https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md` § "Adapter authorship checklist" — the 12 rows you must satisfy

If you change wire-level shapes here without changing the upstream protocol, you have introduced silent incompatibility. Do not.

## What this package is NOT

- Not a Node-only adapter. Do NOT add `import 'node:fs'` (or any `node:`-prefixed import) to any file under `src/` except inside `src/storage/README.md` snippets that are explicitly marked as the contributor pattern. The default ships must run on Cloudflare Workers without polyfills.
- Not a multi-tenant production-grade collector. Storage backends here are reference implementations; consumers wanting strict-monotonic IDs / SQL persistence / S3 / Postgres write their own `IStorage` and inject it.
- Not a rate-limiting / DDoS / WAF layer. The included rate limiter is best-effort and per-IP only; real protection is the consumer's CDN.

## Hard rules

1. **Snake_case JSON over the wire.** No exceptions. Internal-only TS identifiers use camelCase; anything that crosses the network boundary is snake_case.
2. **No silent enum coercion.** Unknown `severity`, `status`, or `report_type` values MUST return 422 `schema_error`. The conformance suite has explicit rejection tests.
3. **Accept deprecated values on read.** A future protocol version may retire enum values; a viewer that refuses to *read* them locks consumers out of historical data forever. Strict on write, lenient on read.
4. **Screenshots flow as `Uint8Array`.** Not `Buffer`, not `Blob`, not `ReadableStream` (except in the storage layer if you want to). The `IStorage.getScreenshotBytes()` return type is the single source of truth.
5. **Server captures User-Agent independently.** Never overwrite `server_user_agent` with the client-supplied `context.user_agent`. The two fields exist precisely so they can be compared.
6. **GitHub sync is best-effort.** Sync failures log and return `github_issue_url: null` — they MUST NOT cause the intake response to be non-2xx.
7. **Intake `201` envelope is minimal.** `{ id, received_at, stored_at, github_issue_url }` only. No echo of user-submitted free text. (Reverse-proxy logs and browser network panels see this body.)
8. **Viewer prefix must be non-empty.** The viewer serves an HTML index at the prefix root; mounting at `/` collides with the consumer's app root. The package throws at startup if you try.
9. **Install `app.onError`.** Hono's default 500 emits plain text, which corrupts the `{error, detail}` envelope. The package wires this up; don't remove it.

## Layout

```
src/
├── index.ts            — public API surface
├── app.ts              — createBugFabApp() / mountBugFab()
├── intake.ts           — POST /bug-reports
├── viewer.ts           — 7 viewer endpoints + HTML pages
├── validation.ts       — wire-protocol validators (snake_case-aware)
├── errors.ts           — `{ error, detail }` envelope factories
├── github.ts           — fetch-based GitHub Issues sync
├── types.ts            — wire-protocol TS types + IStorage interface
├── storage/
│   ├── IStorage.ts     — re-export for type-only imports
│   ├── MemoryStorage.ts — in-memory (tests, POC)
│   ├── R2Storage.ts    — Cloudflare R2 + KV (production)
│   ├── KVStorage.ts    — Cloudflare KV-only (smaller deploys)
│   └── README.md       — guidance for writing custom backends
└── viewer-html/
    └── render.tsx      — Hono JSX components for HTML pages
```

## Common LLM mistakes to avoid

- Camel-casing `received_at` / `client_ts` / `protocol_version` / `github_issue_url` because TS conventions feel that way. **Don't.** The wire format is locked snake_case and the conformance suite checks.
- Writing `screenshotEntry.buffer` or `Buffer.from(...)`. The Web `File` object exposes `.arrayBuffer()`, not `.buffer`. Convert with `new Uint8Array(await file.arrayBuffer())`.
- Returning the full `BugReportDetail` from the intake handler so the client doesn't have to make a second request. Don't. The `201` envelope is privacy-locked to four fields. Clients fetch the detail with `GET /reports/{id}` after intake.
- Adding a `node:fs`-backed default storage to "make it more useful." That breaks Cloudflare Workers at deploy time. Document the file-backed pattern in `src/storage/README.md` instead.
- Coercing unknown severity to `"medium"` ("being lenient"). The conformance suite has an explicit `severity: "urgent"` → 422 test. Lenience here is a bug.
- Forgetting to wrap the JSON.parse / parseBody / arrayBuffer calls in try/catch. Bad inputs are routine; don't let them surface as 500s.
- Removing the `onError` handler in `app.ts`. Hono's default 500 returns plain text, which fails the `{error, detail}` conformance check.

## When extending

- New optional metadata fields can be added without bumping the protocol — preserve them through round-trip and they ride for free.
- New endpoints require a protocol bump (additive, no breaking change).
- New storage backends just implement `IStorage` and live under `src/storage/` — no other code changes needed.
- New runtime examples go under `examples/` and never inside `src/`.

## First-party status

This adapter was promoted to first-party on 2026-05-21 after 44/44 tests passed under `node:20`. It lives in `repo/adapters/hono/` and is published as `bug-fab-hono@0.1.0` tracking protocol `v0.1`. See `docs/ADAPTERS_REGISTRY.md` for the canonical row.

When making changes:

1. Keep the conformance suite green (`npm test`) — never weaken assertions to make the suite pass.
2. Run the official conformance suite (`pytest --bug-fab-conformance --base-url=...`) against a deployed instance before tagging a new release.
3. Tag releases matching the protocol version they track (e.g., `bug-fab-hono@0.1.x` for protocol `v0.1`).
