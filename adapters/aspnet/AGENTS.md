# AGENTS.md — BugFab.AspNetCore

Notes for AI assistants and contributors working on the ASP.NET Core adapter draft.

## What this is

A C# / .NET 8 adapter for the Bug-Fab v0.1 wire protocol. Mirrors the protocol contract and the reference Python adapter's behavior exactly. The wire protocol — not the C# API — is the source of truth.

**Authoritative references** (in priority order):

1. [`docs/protocol-schema.json`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json) — JSON Schema. Wins on disagreement.
2. [`docs/PROTOCOL.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md) — prose spec. Commentary on the schema.
3. [`docs/CONFORMANCE.md`](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/CONFORMANCE.md) — how to run the conformance suite.
4. [`docs/ADAPTERS.md` § ASP.NET Core](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS.md#aspnet-core--razor-pages-net-8) — the upstream sketch this draft expands on.

## Endpoint contract (must match exactly)

| Method | Path (under `RoutePrefix`) | Status (success) |
|---|---|---|
| `POST` | `/bug-reports` | `201` |
| `GET` | `/reports` | `200` |
| `GET` | `/reports/{id}` | `200` |
| `GET` | `/reports/{id}/screenshot` | `200` |
| `PUT` | `/reports/{id}/status` | `200` |
| `DELETE` | `/reports/{id}` | `204` |
| `POST` | `/bulk-close-fixed` | `200` |
| `POST` | `/bulk-archive-closed` | `200` |
| `GET` | `/` (HTML viewer list) | `200` |
| `GET` | `/{id}` (HTML viewer detail) | `200` |

The eight protocol endpoints are the wire-protocol surface. The two HTML pages are convenience surfaces for human operators; consumers may disable them by registering only the JSON endpoints (call `app.MapBugFabApi()` instead of `app.UseBugFab()` — see `BugFabExtensions.cs`).

## Validation rules (NON-NEGOTIABLE)

1. `protocol_version` MUST equal `"0.1"`. Other values → `400 unsupported_protocol_version`.
2. `severity` MUST be one of `low / medium / high / critical`. **No silent coercion** — invalid → `422 schema_error`. Hand-rolled C# implementations frequently include a default-fallback line; **delete it** if you find it.
3. `status` MUST be one of `open / investigating / fixed / closed` on write paths. Read paths accept any string (deprecated-values rule).
4. `report_type` MUST be one of `bug / feature_request`. Invalid → `422`.
5. Screenshot magic bytes MUST match PNG (`89 50 4E 47 0D 0A 1A 0A`). Wrong type → `415 unsupported_media_type`.
6. `reporter.name`, `reporter.email`, `reporter.user_id` MUST be ≤ 256 chars each. Longer → `422`.
7. `title` length MUST be 1–200. `client_ts` MUST be present and non-empty.

## JSON serialization

ASP.NET Core 8 defaults to camelCase. **Bug-Fab requires `snake_case`.** Configure once in `BugFabExtensions.AddBugFab`:

```csharp
options.JsonOptions = new JsonSerializerOptions
{
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    DefaultIgnoreCondition = JsonIgnoreCondition.Never,
};
```

`PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower` is built-in to .NET 8.

The adapter MUST NOT change the host application's global JSON options. Endpoint-level `Results.Json(payload, jsonOptions)` keeps the change local.

## Storage layer

The `IStorage` async interface is the boundary. Two implementations ship:

- `EfCoreStorage` — backed by `BugFabDbContext`. Default for production.
- `FileStorage` — disk-only fallback for tests / demos.

Both implementations MUST:

- Persist the **raw submitted metadata JSON verbatim** (forward-additive: future protocol fields round-trip even if the entity model doesn't know them).
- Persist `protocol_version` on the row, not derived at read time.
- Capture `server_user_agent` from the HTTP request header, not from the client-supplied JSON.
- Append a `created` lifecycle entry on save.
- Append a `status_changed` lifecycle entry on every successful status update.
- Treat `bulk-close-fixed` as idempotent at the per-row level (already-`closed` rows aren't double-counted).

## Threat model

Bug-Fab v0.1 ships no auth abstraction. The adapter's expectation:

- The intake endpoint is typically **open or behind soft auth** so any logged-in user can submit a bug.
- The viewer endpoints (status update, delete, bulk) are typically **admin-only**.
- Mount-point delegation handles this — register the routes inside an `app.MapGroup("/admin").RequireAuthorization(...)` if you want auth.
- CSRF on the viewer's mutating endpoints is the **consumer's responsibility**. The adapter does not auto-validate antiforgery tokens because:
  - Many consumers run Bug-Fab as a backend API consumed by a SPA / Razor page that handles tokens differently.
  - Adding `[ValidateAntiForgeryToken]` would force a specific cookie/token shape.
  - The intake endpoint cannot use antiforgery (cross-page bug submission).

If you need CSRF protection on the viewer routes, wrap them in middleware that calls `IAntiforgery.ValidateRequestAsync(...)`.

### Antiforgery anti-patterns (DO NOT)

1. **Do not call `.RequireAntiforgery()` on the intake group.** The JS bundle posts cross-origin multipart from the host page; antiforgery would reject every legitimate submission. Intake's `.DisableAntiforgery()` is load-bearing.
2. **Do not silently auto-wire `IAntiforgery` on the viewer group in v0.1.** `BugFabOptions.EnableAntiforgeryOnViewer` is declared but intentionally not wired in v0.1 — wiring it requires picking a token shape (cookie / header / SPA double-submit / etc.) that consumer auth setups disagree on. v0.2's `AuthAdapter` work owns this. Adding a half-baked v0.1 wiring would force consumers to redo it later.
3. **Do not add `[ValidateAntiForgeryToken]` attributes** to the viewer endpoints. Same shape-locking concern, plus the attribute is MVC-flavored and the adapter is Minimal API.
4. **Do not omit the README CSRF section** when refactoring docs. The asymmetric default (intake disabled, viewer not protected) is the kind of trade-off a consumer can ship a hole on by skimming. The "⚠ CSRF / Antiforgery — read this first" section near the top of the README is intentionally placed before the configuration block so a tutorial-skimming reader can't miss it.

## Known divergences from the upstream sketch in `ADAPTERS.md`

The ADAPTERS.md sketch uses controllers (`[ApiController]`). This draft uses **Minimal API endpoints** because:

- They're cleaner for a small fixed endpoint set.
- They avoid the routing-attribute boilerplate.
- They make `RequireAuthorization()` per-endpoint trivial.

Both approaches are conformant. The sketch in `ADAPTERS.md` is illustrative; the package picked the cleaner of the two.

## Rate limiting (intake only)

The intake endpoint is gated by a fixed-window per-IP limiter using `Microsoft.AspNetCore.RateLimiting` (built-in to ASP.NET Core 8 — **do not** introduce a third-party limiter package).

Wiring lives in `BugFabExtensions.AddIntakeRateLimiter` and is gated by `BugFabOptions.RateLimit.Enabled`:

- `services.AddRateLimiter(...)` is only called when the flag is true. Calling it conditionally avoids registering the middleware in apps that don't want it.
- The policy name is `BugFabExtensions.IntakeRateLimitPolicy` (`"bug-fab-intake"`). Public so consumers can compose with their own policies.
- The partition key is the client IP, derived from `X-Forwarded-For` first hop, then `Connection.RemoteIpAddress`, then `"unknown"`. Mirrors the Python reference's `_client_ip` helper in `bug_fab/routers/submit.py`.
- `OnRejected` writes `ErrorEnvelope { Error = "rate_limited", Detail, RetryAfterSeconds }` via `Response.WriteAsJsonAsync` using `BugFabOptions.JsonOptions`. **Do NOT** let ASP.NET Core's default 429 response (text/plain "Too Many Requests") slip through — conformance tests pin the JSON shape.
- `UseBugFab` calls `app.UseRateLimiter()` and applies `intake.RequireRateLimiting(IntakeRateLimitPolicy)` only when `Enabled` is true. Viewer endpoints are not gated.

Future contributors adding new rate-limit features (bursting, per-user identity, distributed state) should extend `RateLimitOptions` rather than introduce a parallel knob; the v0.2 `AuthAdapter` work will replace the IP-based partition with a user-identity partition.

## Templates

Razor views in `Views/` are minimal — they render the data needed for the eight endpoints. They are **not** a port of the upstream Jinja2 templates' styling. Consumers wanting pixel-parity with the Python reference viewer should override the views or fork them.

### Path discipline (DO NOT hard-code `~/bug-fab/`)

The cshtml files MUST stay prefix-aware. The viewer is mountable under any non-empty `RoutePrefix` (`/bug-fab`, `/admin/bug-reports`, `/qa/issues`, …) and hard-coded paths break the moment a consumer picks something other than the default.

**Rules:**

1. **Use `@Url.RouteUrl("BugFab_*", new { ... })` for links to other Bug-Fab routes.** Each Minimal API endpoint declares a `WithName("BugFab_*")` annotation; the route names are the canonical interface for cross-referencing them from views. Available names: `BugFab_HtmlList`, `BugFab_HtmlDetail`, `BugFab_Screenshot`, `BugFab_Intake`, `BugFab_List`, `BugFab_Detail`, `BugFab_StatusUpdate`, `BugFab_Delete`, `BugFab_BulkCloseFixed`, `BugFab_BulkArchiveClosed`.
2. **Use same-folder relative URLs (`./{id}`, `./`) as a fallback** when the view sits at the route the link is relative to. `ViewerHtmlEndpoints.cs` does this — it works regardless of mount prefix because the `<a href="./{id}">` resolves against the request URL.
3. **Do NOT hard-code `~/bug-fab/...`.** It silently breaks every non-default mount. The audit (`docs/audits/2026-05-01_aspnet_adapter_audit.md` § I) caught the original draft doing this; the fix replaced every occurrence with `@Url.RouteUrl(...)`.
4. **The `~/bug_fab/bug-fab.css` and `~/bug_fab/bug-fab.js` paths in `_BugFabLayout.cshtml` are the exception** — those resolve against `wwwroot/bug_fab/`, which is the static-file directory the package vendors. That path is fixed and is NOT the same as `RoutePrefix`. Don't conflate the two.

## Testing

The `tests/BugFab.AspNetCore.Tests` project builds an in-process `WebApplication` directly via the static `TestApp.BuildApp(...)` helper (rather than the `WebApplicationFactory<T>` plumbing — that pattern is geared toward an existing `Program.cs` MVC host, which this adapter doesn't have). Tests cover:

- Intake validation (severity rejection, PNG magic bytes, protocol version, size limits)
- Viewer JSON shapes and pagination
- Bulk action idempotency

The conformance suite (Python pytest plugin) runs against a launched server — it's outside the .NET test runner. CI runs both.

## Subagent constraints (when this draft was created)

This draft was created by an AI subagent without access to a .NET toolchain. It has not been compiled or executed. Treat it as a structural reference; expect the first `dotnet build` to surface real-world issues. The structural conformance to the wire protocol is what the upstream conformance suite will verify; the C# itself needs the standard "first compile" pass.
