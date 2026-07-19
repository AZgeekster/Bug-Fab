# BugFab.AspNetCore

ASP.NET Core 8 adapter for the [Bug-Fab](https://github.com/AZgeekster/Bug-Fab) wire protocol v0.1.

> Status: **draft**. Conformance-verified 2026-07-12 — the upstream `pytest --bug-fab-conformance` suite passes 32/32 against `examples/MinimalApi` via [`conformance/run-conformance.sh`](./conformance/). Tracks Bug-Fab v0.1.

This package wires the eight Bug-Fab endpoints into an ASP.NET Core 8 application as Minimal API endpoints, with a default Entity Framework Core storage backend supporting SQL Server and PostgreSQL.

The adapter implements the protocol contract; **consumers are responsible for authentication**. Mount the routes inside your existing auth middleware or call `.RequireAuthorization(...)` on the returned `IEndpointConventionBuilder`.

---

## Install

```sh
dotnet add package BugFab.AspNetCore --prerelease
```

You also need:

- `Microsoft.EntityFrameworkCore` (8.x)
- One EF Core provider — `Microsoft.EntityFrameworkCore.SqlServer` *or* `Npgsql.EntityFrameworkCore.PostgreSQL`
- `Microsoft.AspNetCore.Mvc.Razor` (only if you mount the HTML viewer pages)

## Quickstart

```csharp
using BugFab.AspNetCore;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// Bug-Fab DI registration. Reads the "BugFab" section from appsettings.json.
builder.Services.AddBugFab(builder.Configuration, options =>
{
    options.RoutePrefix          = "/bug-fab";
    options.StorageDirectory     = "./var/bug-fab";
    options.MaxScreenshotBytes   = 10 * 1024 * 1024;   // 10 MiB
    options.UseEfCoreStorage     = true;
});

builder.Services.AddDbContext<BugFabDbContext>(opts =>
    opts.UseSqlServer(builder.Configuration.GetConnectionString("BugFab")));

var app = builder.Build();

app.UseBugFab();    // mounts the eight endpoints under options.RoutePrefix

app.Run();
```

That's it — submit a report by POSTing to `/bug-fab/bug-reports`, view the list at `/bug-fab/`.

---

## ⚠ CSRF / Antiforgery — read this first

**Read this before mounting Bug-Fab on a public host.** The default antiforgery posture is asymmetric and easy to miss:

1. **Intake (`POST /bug-reports`) disables antiforgery on purpose.** The JS bundle posts cross-origin from the host page (multipart `form-data` + screenshot blob). ASP.NET Core's antiforgery middleware would reject every legitimate submission. The intake endpoint calls `.DisableAntiforgery()` so this works.
2. **Viewer mutating endpoints are NOT antiforgery-protected by default.** The four state-changing viewer routes — `PUT /reports/{id}/status`, `DELETE /reports/{id}`, `POST /bulk-close-fixed`, `POST /bulk-archive-closed` — accept JSON. ASP.NET Core 8's default behavior is "JSON Minimal API endpoints don't auto-validate antiforgery", so these endpoints ship **un-protected** unless your app adds protection.
3. **Recommended pattern**: gate the viewer mount prefix behind your host app's existing authentication. A logged-in admin session that hits `DELETE /admin/bug-fab/reports/bug-001` is already protected by the same-site cookie + your auth middleware; CSRF is moot if a non-admin user can't reach the route. This is the pattern Bug-Fab v0.1 assumes.

   ```csharp
   var bugFab = app.UseBugFab();
   bugFab.Viewer.RequireAuthorization("BugFabAdmin");   // viewer behind admin auth
   bugFab.Intake.AllowAnonymous();                       // intake open or behind soft auth
   ```
4. **Optional `EnableAntiforgeryOnViewer` config flag** (v0.2 — declared today, wired in v0.2). When v0.2 ships, setting `BugFab:EnableAntiforgeryOnViewer = true` will wire `RequireAntiforgery()` on the viewer group automatically. The flag is reserved in the v0.1 options shape so consumers writing config today don't need to change shape later.

**If you mount the viewer without auth on a public host, you have CSRF holes.** Bug-Fab does NOT auto-validate antiforgery tokens on the viewer because consumer auth setups vary too much (cookie auth + tokens, JWT, header-based session, SPA + token-in-header, etc.) to pick one shape; v0.2's `AuthAdapter` will close this gap with a configurable strategy.

See [`AGENTS.md`](./AGENTS.md) § "Threat model" for the full trade-off and rationale.

---

## Configuration (`appsettings.json`)

```json
{
  "BugFab": {
    "RoutePrefix": "/bug-fab",
    "StorageDirectory": "./var/bug-fab",
    "MaxScreenshotBytes": 10485760,
    "MaxMetadataBytes": 262144,
    "UseEfCoreStorage": true,
    "GitHub": {
      "Enabled": false,
      "Repository": "owner/repo",
      "PersonalAccessToken": ""
    },
    "ViewerPermissions": {
      "CanEditStatus": true,
      "CanDelete": true,
      "CanBulk": true
    },
    "RateLimit": {
      "Enabled": false,
      "MaxPerWindow": 30,
      "WindowSeconds": 60
    },
    "EnableAntiforgeryOnViewer": false
  },
  "ConnectionStrings": {
    "BugFab": "Server=.;Database=BugFab;Trusted_Connection=True;TrustServerCertificate=True"
  }
}
```

PostgreSQL example:

```json
"ConnectionStrings": {
  "BugFab": "Host=localhost;Database=bugfab;Username=bugfab;Password=secret"
}
```

…with `opts.UseNpgsql(...)` instead of `UseSqlServer(...)`.

---

## Authentication

`BugFab.AspNetCore` is **auth-agnostic**. The package does not include or require any specific identity provider. Apply policies the standard ASP.NET Core way:

```csharp
// Whole adapter behind auth
app.UseBugFab().RequireAuthorization();

// Different policies for intake vs viewer
var bugFab = app.UseBugFab();
bugFab.RequireAuthorization("BugFabSubmit");
// (intake routes returned by UseBugFab include both intake + viewer; gate further with middleware)
```

If you need finer-grained gating (e.g., `[Authorize(Roles = "Admin")]` on the viewer but open intake), pass an `Action<IEndpointConventionBuilder>` per group via the options:

```csharp
app.UseBugFab(g =>
{
    g.Intake.AllowAnonymous();
    g.Viewer.RequireAuthorization("BugFabAdmin");
});
```

---

## Rate limiting

Set `BugFab:RateLimit:Enabled` to `true` to gate the intake endpoint with a per-IP fixed-window rate limiter. The limiter is wired through `Microsoft.AspNetCore.RateLimiting` (built-in to ASP.NET Core 8) — the adapter does **not** introduce a third-party limiter package.

```json
"RateLimit": {
  "Enabled": true,
  "MaxPerWindow": 30,
  "WindowSeconds": 60
}
```

When the limiter rejects a request, the response is `429 Too Many Requests` with the protocol error envelope:

```json
{
  "error": "rate_limited",
  "detail": "Rate limit exceeded: max 30 reports per 60 seconds",
  "retry_after_seconds": 60
}
```

Notes:

- Only the intake endpoint (`POST /bug-reports`) is rate-limited. Viewer endpoints are not.
- The per-IP partition key is the connection's resolved client address (`HttpContext.Connection.RemoteIpAddress`). Raw `X-Forwarded-For` is deliberately not read — the header is client-controlled, and rotating it would mint a fresh bucket per request and defeat the limiter. Deployments behind a reverse proxy should register ASP.NET Core's [`ForwardedHeadersMiddleware`](https://learn.microsoft.com/aspnet/core/host-and-deploy/proxy-load-balancer) with `KnownProxies`/`KnownNetworks`; it rewrites `RemoteIpAddress` from the forwarding chain only when the direct peer is a declared proxy, and the partition key then meters per-end-user. Without it, metering is per-proxy.
- The state is process-local. Multi-worker deployments scale the effective limit linearly with the worker count; front-door rate limiting (nginx, Cloudflare, etc.) remains the right answer for hard abuse boundaries.
- The policy name is exposed as `BugFabExtensions.IntakeRateLimitPolicy` (`"bug-fab-intake"`) for consumers who want to compose it with their own limiter configuration.

---

## Antiforgery and CSRF

**See the prominent [CSRF / Antiforgery](#-csrf--antiforgery--read-this-first) section near the top of this README** — that's the canonical write-up of the trade-off. Short version restated here for the table-of-contents reader who scrolled to this section directly:

- Intake (`POST /bug-reports`) disables antiforgery (cross-page multipart submission). Required.
- Viewer mutating endpoints (`PUT /reports/{id}/status`, `DELETE /reports/{id}`, the bulk endpoints) are JSON and ship **without** antiforgery validation in v0.1.
- Recommended defense: require host-app authentication on the viewer mount prefix (`bugFab.Viewer.RequireAuthorization(...)`). If admins are the only ones who can reach the route, CSRF on those routes is largely moot because a CSRF attack from a non-admin browser session can't authenticate.
- `BugFabOptions.EnableAntiforgeryOnViewer` is declared today (defaults to `false`) but **not yet wired** — v0.2 will wire `RequireAntiforgery()` on the viewer group when this flag is set. The shape is reserved so v0.1 config files don't need to change later.
- The adapter does NOT auto-validate antiforgery tokens because consumer auth setups vary too much for a single shape to fit.

See [`AGENTS.md`](./AGENTS.md) § "Threat model" for the trade-off in detail.

---

## EF Core migrations

The package ships a single initial migration (`20260501_Initial`). Add it to your DbContext or run as a tool:

```sh
dotnet ef database update --context BugFabDbContext
```

The migration creates two tables and one sequence:

- `bug_fab_bug_reports` — one row per report
- `bug_fab_bug_report_lifecycle` — append-only audit log
- `bug_report_id_seq` — HiLo-managed sequence backing the `bug-NNN` wire ID generator (collision-free under concurrent intake; see `EfCoreStorage` doc-comment)

Both tables are namespaced with the `bug_fab_` prefix to avoid colliding with consumer tables. The sequence is named without that prefix because EF Core's `UseHiLo("bug_report_id_seq")` registration configures it at the model level and consumer apps don't typically declare their own sequences.

---

## Storage backends

Two backends ship in v0.1:

| Backend | When to use |
|---|---|
| **`EfCoreStorage`** (default) | Production. Requires a configured `BugFabDbContext`. SQL Server + PostgreSQL supported. |
| **`FileStorage`** | Tests, demos, single-process deployments. Stores everything under `options.StorageDirectory`. |

Switch via `options.UseEfCoreStorage = false` (or by registering your own `IStorage` implementation before `AddBugFab` runs).

---

## Mount-prefix invariant

The viewer mounts an HTML list at `{RoutePrefix}/`. Per the [Bug-Fab adapter authorship checklist](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#adapter-authorship-checklist), `RoutePrefix` MUST NOT be empty — set it to `/bug-fab`, `/admin/bug-reports`, or any non-root prefix the routing table can attach an HTML page to. The `AddBugFab` call throws `ArgumentException` on an empty prefix.

### Prefix-aware URLs in views

Both viewer paths are **prefix-aware** — change `RoutePrefix` and the links keep working without per-template edits:

- The default inline-HTML viewer (`ViewerHtmlEndpoints.cs`) emits relative URLs (`./{id}`, `./reports/{id}/screenshot`, `./`) so the prefix is honored automatically.
- The optional Razor viewer (`Views/*.cshtml`) uses named-route helpers (`@Url.RouteUrl("BugFab_HtmlDetail", new { id = ... })`, `@Url.RouteUrl("BugFab_HtmlList")`, `@Url.RouteUrl("BugFab_Screenshot", new { id = ... })`). Each Minimal API endpoint declares a `WithName("BugFab_*")` annotation that the helpers resolve to the actual route under whatever prefix is configured.

If you fork the Razor views and add new links, **do not hard-code `~/bug-fab/...`** — call `@Url.RouteUrl(routeName, routeValues)` (or use a same-folder relative URL like `./{id}`) so consumers mounting at `/admin/bugs` aren't broken. The full set of route names is in `BugFabExtensions.cs`.

---

## What's *not* in this package

- No JS frontend bundle is generated by this package — copy `bug-fab.js` from the upstream Bug-Fab repo's `static/` directory at build time. See [`src/BugFab.AspNetCore/wwwroot/bug_fab/README.md`](./src/BugFab.AspNetCore/wwwroot/bug_fab/README.md).
- No SignalR / hub integration. Bug-Fab v0.1 is request/response.
- No authentication or identity provider. See [Authentication](#authentication).
- No CSRF token auto-issuing. See [Antiforgery and CSRF](#antiforgery-and-csrf).

---

## Conformance

**Passing 32/32** as of 2026-07-12 — `./conformance/run-conformance.sh` boots
`examples/MinimalApi` in a `dotnet/sdk:8.0` container and runs
`pytest --bug-fab-conformance` from a sibling `python:3.12` container. See
[`conformance/README.md`](./conformance/README.md).

To run the suite by hand against your own running app:

```sh
pip install --pre bug-fab
pytest --bug-fab-conformance --base-url=http://localhost:5000/bug-fab
```

Per the [adapter authorship checklist](https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md#adapter-authorship-checklist), this 12-point checklist must pass before promotion from sketch → community-maintained.

---

## License

MIT — see [`LICENSE`](./LICENSE).
