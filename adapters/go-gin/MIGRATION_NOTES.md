# Migration & Operational Notes

Things that don't fit in README.md but matter for anyone running this adapter alongside the Python reference or in front of a real consumer.

## Bytes-on-disk are cross-readable

The default `FileStorage` writes exactly the same file shape as the Python reference (`bug-NNN.json`, `bug-NNN.png`, `index.json`). Either adapter can read either's storage dir. This means:

- You can run the Python adapter for intake and this Go adapter for the viewer (or vice versa) against one shared directory.
- A future tooling pass can validate cross-readability by spinning up both adapters against the same fixture set.
- A migration from Python -> Go (or back) is a `cp -r` of the storage dir, not a data conversion.

If you change the file shape, run the cross-readability test in `storage_test.go` and update the Python reference's `files.py` in lockstep.

## Field-naming intentionally mirrors PROTOCOL.md

JSON tags use snake_case throughout because the wire protocol is snake_case in both directions. Go consumers should expect `bug.Severity` on the struct, `"severity"` on the wire. Do not rename struct fields to camelCase even when refactoring — the JSON tags would also need to change and the protocol would drift.

## Reporter cap is 256 chars per sub-field, not per object

`Reporter.Name`, `Reporter.Email`, and `Reporter.UserID` are each capped at 256 characters independently. Three 200-character values is fine; one 257-character `user_id` is a 422. This matches the Python reference's `Pydantic` `Field(max_length=256)` on each.

## Context extras survive round-trip — do not drop them

Consumer-specific diagnostic fields land in `BugReportContext.Extras` and are re-emitted on marshal. Do not "clean up" or filter Extras anywhere downstream — the protocol's forward-additive guarantee depends on extras surviving every storage layer.

## Server User-Agent is the source of truth

The intake handler captures `User-Agent` from the request headers and stores it as `server_user_agent`. The value the client sent in `context.user_agent` is preserved separately as `client_reported_user_agent`. Never overwrite `server_user_agent` with the client value, even if it looks more useful — the audit trust boundary depends on the distinction.

## Rate limiter is in-process only

`RateLimiter` keeps state in a `sync.Mutex`-guarded map. Multi-instance deployments will overcount the limit because each instance has its own counter. If you need cluster-wide limiting, plug in a Redis-backed limiter by implementing a tiny interface and swapping it in (the interface isn't exported yet — a v0.2 improvement).

## Test fixture: the PNG signature lives in tinyPNG

The unit tests don't generate a real PNG with `image/png` — they use a minimal 67-byte buffer (`tinyPNG` in `storage_test.go`) that satisfies the magic-byte sniff. If you tighten validation to a full PNG parse (e.g., via the `image/png` decoder), update this fixture; otherwise every test breaks.

## Errors don't bubble Gin's defaults — they're shaped manually

Every non-2xx path in `routes.go` writes the `ErrorEnvelope` shape explicitly. Do not switch to `c.AbortWithError(...)` or `gin.H{...}` — the wire envelope is contract and arbitrary shapes break consumer parsers.

## v0.2 candidates (not yet implemented)

- AuthAdapter interface (mirrors the Python ABC)
- GitHub Issues sync (best-effort, deferred)
- Webhook sync (Slack / Linear / n8n)
- PostgreSQL storage backend
- Token-bucket rate limiter (in addition to the fixed-window default)
- Conformance pytest harness with a Go test runner
