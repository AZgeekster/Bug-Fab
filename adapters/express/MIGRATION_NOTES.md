# Migration Notes — Express / Node specifics

Things future consumers should know that don't fit in the README.

## Multer memory storage, not disk storage

The intake handler configures multer with `memoryStorage()` and reads the
PNG bytes out of `req.file.buffer`. We deliberately do not use multer's
`diskStorage()` for two reasons:

* The wire protocol's PNG magic-byte check has to run *before* we agree
  to persist anything. Disk storage would have already written the
  attacker-controlled bytes to `/tmp` by the time we look at them.
* The storage backend (`IStorage`) is the only thing that should touch
  the filesystem layout — letting multer pick its own temp path
  fragments the persistence story and complicates clean-up on rejection.

If a consumer needs to support screenshots beyond the protocol's 10 MiB
cap (or wants streaming-to-disk for very large attachments in v0.2+),
they can subclass the multer config rather than swap to disk storage.

## IStorage interface contract

The `IStorage` interface (9 methods + an optional `setGitHubIssue`
post-save hook) is the only persistence surface a custom backend has to
implement. `FileStorage` is the reference implementation; an SQL backend
typically runs ~150 lines.

Method-by-method invariants:

* `saveReport` assigns an id matching `^bug-[A-Za-z]?\d{3,}$`. The
  `idPrefix` option is single-character; backends are free to ignore it
  if they manage their own id space.
* `listReports` MUST compute the `stats` block over the pre-paginated,
  pre-status-filtered set (so the badge counts stay stable as the user
  flips between status filters).
* `bulkCloseFixed` / `bulkArchiveClosed` return only the count of
  reports actually transitioned; no-ops are not counted.
* `setGitHubIssue` is best-effort and idempotent. Backends that don't
  care about GitHub sync can leave it as a no-op.

## Mount-point auth delegation

Bug-Fab v0.1 ships no auth abstraction. The Express adapter exposes the
intake route and the viewer routes from a single `Router`, so the host
app decides where (and behind which middleware) to mount it. The
README's "Auth — protect routes at the mount point" section documents
the three common patterns; the adapter itself takes no opinion.

In v0.2 a proper `AuthAdapter` interface will let the adapter ask "who
is logged in?" so lifecycle audit entries carry server-derived identity
instead of `"anonymous"`. Until then, mount-point delegation is the
contract.

## ESM, `ts-node`, and `tsx`

`package.json` declares `"type": "module"` and the build emits ESM. The
example server uses relative imports with explicit `.js` extensions
(TypeScript's `"module": "NodeNext"` requires this even in `.ts`
sources). For ad-hoc execution we recommend `tsx` over `ts-node` — `tsx`
handles the ESM extension dance without extra flags. The published
package consumes from `dist/` after `tsc`, so end users never see this
indirection.

## TypeScript build target

`tsconfig.json` targets `ES2022` and emits `NodeNext` modules. This
aligns with Node 20's baseline (native `fetch`, top-level `await`, the
WHATWG URL parser). Down-leveling to ES2020 would mean polyfilling
`fetch`, which the README explicitly disclaims; raising past ES2022
would lock out Node 20 LTS users prematurely.

## No server startup at import time

`src/index.ts`, `src/router.ts`, and the rest of the source tree do
**not** call `app.listen` or read environment variables at import time.
Importing `bug-fab-express` is side-effect-free. The host app remains
in full control of the HTTP listener — including TLS termination, port
selection, and graceful shutdown.

`FileStorage` does eagerly create the storage directory in its
constructor (via `mkdirSync(..., { recursive: true })`) so the on-disk
index can be loaded immediately. This is the only filesystem side
effect at adapter wire-up. Tests and ephemeral harnesses that should
not touch real disk must point `storageDir` at a temp path (or supply
their own in-memory `IStorage` implementation).
