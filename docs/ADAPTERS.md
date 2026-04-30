# Bug-Fab Adapters — non-Python reference sketches

Bug-Fab v0.1 ships a Python (FastAPI) reference adapter. This document gives you **reference sketches** for implementing the [Bug-Fab wire protocol](./PROTOCOL.md) in four other stacks:

- [ASP.NET Core / Razor Pages](#aspnet-core--razor-pages-net-8)
- [Express / Node.js](#express--nodejs)
- [SvelteKit](#sveltekit)
- [Go](#go-nethttp--chi)

Each sketch is **code-level enough to be useful**, not a full implementation. Treat them as starting points: real adapters will need additional polish around config, logging, and error handling specific to your stack's conventions.

After implementing, run the [conformance suite](./CONFORMANCE.md) against your adapter to verify protocol compliance.

> The first non-Python adapter to ship as a maintained package will be selected based on which consumer commits to integrating it first. See [`ROADMAP.md`](./ROADMAP.md) § v0.2. Until then, these sketches are the canonical reference.

---

## What every adapter MUST implement

Before diving into stack-specific code, here is the universal checklist. Every endpoint listed in [`PROTOCOL.md`](./PROTOCOL.md) must be reachable; every documented error response must fire correctly; every documented field must round-trip.

| Concern | Requirement |
|---------|-------------|
| Multipart parsing | Accept `multipart/form-data` with `metadata` (JSON string) and `screenshot` (PNG file). Enforce the 11 MiB total-body limit at the framework's parser-registration level (Fastify's `@fastify/multipart` `limits.fileSize`, ASP.NET's `[RequestSizeLimit]`, Express's `multer({ limits })`, etc.) — checking it inside the route handler is too late, the framework already buffered the bytes. |
| JSON validation | Reject invalid `severity` / `status` enum values with `422`. **Do not silently coerce.** Required submission fields are `protocol_version`, `title`, `client_ts` — reject `422` on missing. |
| Protocol versioning | Reject unknown `protocol_version` with `400 unsupported_protocol_version`. |
| Deprecated values | Accept any deprecated enum value on **read** paths. Reject on write paths. |
| User-Agent | Capture from request header (source of truth). Preserve client-supplied `client_reported_user_agent` separately. |
| Storage | Persist atomically. Screenshots on disk; metadata in JSON file or DB row. Never blob-in-DB. If the storage layer uses an in-memory counter or index (file-backed default in the Python reference), document loudly that it is **not safe for multi-worker / cluster-mode deployments** and point users at a SQL backend. The reference `FileStorage` is single-process; `SQLiteStorage` is process-safe with WAL; `PostgresStorage` is freely multi-worker. |
| GitHub sync | Best-effort — failures log, do not break intake. The submit flow is: (1) save report → get `id`; (2) call GitHub API; (3) **post-save update** the stored report's `github_issue_url` + `github_issue_number` fields once the issue resolves. Storage backends SHOULD expose a `set_github_link(id, issue_number, issue_url)` method for the post-save update; the Python reference adapter duck-types it via `getattr(storage, "set_github_link", None)` so adapters that want to skip it just don't implement it. |
| Lifecycle audit log | Append `created` on intake. Append `status_changed` on every status update. Never mutate or remove entries. |
| Bulk ops | `POST /bulk-close-fixed` and `POST /bulk-archive-closed` must return correct counts. |
| Auth | Adapter exposes routes; consumer protects them at the mount point. v0.1 has no auth abstraction. |
| Viewer mount prefix | The viewer router exposes a root HTML list at the empty path (`GET ""` resolved against its mount prefix). Adapter authors MUST mount the viewer under a non-empty URL prefix so the root list has a reachable address. See [Viewer mount-prefix note](#viewer-mount-prefix-note) below. |
| Conformance | Adapter passes the [conformance suite](./CONFORMANCE.md). |

---

## Viewer mount-prefix note

The viewer router serves an HTML list at its **root** path. In the
reference Python adapter, this is declared as
`@viewer_router.get("")` — the empty path that resolves against
whatever URL prefix the consumer mounts the router under. FastAPI
specifically refuses to register such a route if the prefix is also
empty (`FastAPIError: Prefix and path cannot be both empty`), so the
viewer cannot be mounted at the application root.

The constraint generalizes to other stacks: every viewer adapter
needs a reachable address for the list page. ASP.NET Core's attribute
routing, Express's `app.use(...)`, SvelteKit's filesystem routing, and
Go's `chi` mux all behave well with a prefix like `/admin`,
`/bug-fab`, or similar. The pattern adapter authors should follow:

- Mount the viewer under a non-empty URL prefix (`/admin`,
  `/admin/bug-fab`, `/internal/bug-reports`, etc.).
- Reserve the empty path *within* the viewer's prefix for the HTML
  list page.
- The intake router can mount at any prefix — intake declares its own
  path (`POST /bug-reports`), so the empty-prefix concern doesn't
  apply.

Common consumer mount patterns:

| Intake prefix | Viewer prefix | Effect |
|---|---|---|
| `/api` | `/admin` | Open submit, admin-only viewer. The expected default. |
| `/admin` | `/admin` | Both behind admin auth. Intake at `/admin/bug-reports`, viewer list at `/admin/`. |
| `""` (root) | `/admin` | Intake at root (`POST /bug-reports`), viewer at `/admin/`. |

If you're writing a non-Python adapter, the analogous rule: don't let
your viewer's list page collapse to the bare application root. Either
require a prefix at mount time or fail loudly at boot if one isn't
provided.

---

## ASP.NET Core / Razor Pages (.NET 8)

The Razor / Web API sketch below covers the full v0.1 surface using ASP.NET Core 8 and Entity Framework Core.

### Route layout

Two controllers, mounted under different route prefixes so the consumer can apply different `[Authorize]` policies to each:

```csharp
// Mount intake at /api/ — typically auth-required for any logged-in user
[ApiController]
[Route("api/bug-reports")]
public class BugReportsIntakeController : ControllerBase { ... }

// Mount viewer at /admin/ — typically auth-required for admin role only
[ApiController]
[Route("admin/reports")]
[Authorize(Roles = "BugFabAdmin")]
public class BugReportsViewerController : ControllerBase { ... }
```

### Intake endpoint

```csharp
[HttpPost]
[RequestSizeLimit(11 * 1024 * 1024)] // 11 MiB
public async Task<IActionResult> Submit(
    [FromForm] string metadata,
    IFormFile screenshot,
    CancellationToken ct)
{
    if (string.IsNullOrEmpty(metadata) || screenshot is null)
        return BadRequest(new { error = "validation_error",
                                detail = "metadata and screenshot are both required" });

    if (screenshot.ContentType != "image/png")
        return StatusCode(415, new { error = "unsupported_media_type" });

    if (screenshot.Length > 10 * 1024 * 1024)
        return StatusCode(413, new { error = "payload_too_large",
                                     limit_bytes = 10 * 1024 * 1024 });

    BugReportMetadata? parsed;
    try
    {
        parsed = JsonSerializer.Deserialize<BugReportMetadata>(metadata, JsonOpts);
    }
    catch (JsonException ex)
    {
        return BadRequest(new { error = "validation_error", detail = ex.Message });
    }

    if (parsed?.ProtocolVersion != "0.1")
        return BadRequest(new { error = "unsupported_protocol_version" });

    // Strict severity validation. NO silent coercion.
    if (parsed.Severity is not null &&
        !ValidSeverities.Contains(parsed.Severity))
        return UnprocessableEntity(new { error = "schema_error",
                                         detail = $"severity must be one of: {string.Join(", ", ValidSeverities)}" });

    // Capture User-Agent from the request header — source of truth.
    var serverUserAgent = HttpContext.Request.Headers.UserAgent.ToString();

    var report = await _service.SaveAsync(parsed, screenshot, serverUserAgent, ct);

    return Created($"/admin/reports/{report.Id}", new
    {
        id = report.Id,
        received_at = report.ReceivedAt,
        stored_at = report.StoredAt,
        github_issue_url = report.GitHubIssueUrl
    });
}

private static readonly HashSet<string> ValidSeverities =
    new(StringComparer.Ordinal) { "low", "medium", "high", "critical" };
```

### EF Core entities

Mirror the schema from [`PROTOCOL.md`](./PROTOCOL.md) and the canonical SQL shape used by Bug-Fab's reference Postgres backend:

```csharp
public class BugReport
{
    public string Id { get; set; } = default!;          // bug-NNN
    public DateTimeOffset ReceivedAt { get; set; }
    public string ProtocolVersion { get; set; } = "0.1";
    public string Title { get; set; } = default!;
    public string Description { get; set; } = default!;
    public string? Severity { get; set; }               // CHECK: low|medium|high|critical
    public string Status { get; set; } = "open";        // CHECK: open|investigating|fixed|closed
    public string? Environment { get; set; }            // free string
    public string? AppName { get; set; }
    public string? AppVersion { get; set; }
    public string? ReporterEmail { get; set; }
    public string PageUrl { get; set; } = default!;
    public string? UserAgentServer { get; set; }        // captured from request header
    public string? UserAgentClient { get; set; }        // client-reported
    public string MetadataJson { get; set; } = default!;
    public string ScreenshotPath { get; set; } = default!;
    public string? GitHubIssueUrl { get; set; }
    public int? GitHubIssueNumber { get; set; }
    public DateTimeOffset? ArchivedAt { get; set; }

    public List<BugReportLifecycle> Lifecycle { get; set; } = new();
}

public class BugReportLifecycle
{
    public int Id { get; set; }
    public string BugReportId { get; set; } = default!;
    public string Action { get; set; } = default!;     // created|status_changed|deleted|archived
    public string? By { get; set; }
    public DateTimeOffset At { get; set; }
    public string? FixCommit { get; set; }
    public string? FixDescription { get; set; }
    public string? MetadataJson { get; set; }
}
```

### GitHub Issues sync

Use `IHttpClientFactory` to issue token-authenticated requests against the GitHub REST API. Run the call **outside** any DB transaction so a slow GitHub does not back up local writes:

```csharp
public class GitHubIssueService : IGitHubIssueService
{
    private readonly HttpClient _client;
    private readonly GitHubSettings _settings;
    private readonly ILogger<GitHubIssueService> _log;

    public async Task<(int? number, string? url)> CreateIssueAsync(BugReport report, CancellationToken ct)
    {
        if (!_settings.Enabled || string.IsNullOrEmpty(_settings.PersonalAccessToken))
            return (null, null);

        try
        {
            var body = new
            {
                title = $"[Bug] {report.Title}",
                body  = BuildIssueBody(report),
                labels = new[] { "bug", $"severity:{report.Severity ?? "medium"}" }
            };

            var resp = await _client.PostAsJsonAsync(
                $"https://api.github.com/repos/{_settings.Repository}/issues", body, ct);
            resp.EnsureSuccessStatusCode();

            var issue = await resp.Content.ReadFromJsonAsync<GitHubIssueResponse>(cancellationToken: ct);
            return (issue?.Number, issue?.HtmlUrl);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "GitHub issue creation failed for report {Id}", report.Id);
            return (null, null);  // best-effort — never propagate
        }
    }
}
```

### CC16 verification — what this sketch must cover

The .NET reference-consumer audit (CC16) requires this sketch to demonstrate five capabilities so a consumer migrating from a hand-rolled implementation can plug Bug-Fab in cleanly:

| Capability | Where in this sketch |
|------------|---------------------|
| **Data-path config** | `appsettings.json` exposes `BugFab:DataPath`; `IOptions<BugFabSettings>` injected into the service. |
| **Role-based auth gate** | `[Authorize(Roles = "...")]` on the viewer controller. Consumer chooses which roles. |
| **Source-mapper hook** | The intake controller calls `_sourceMapper.Map(parsed.PageUrl)` before persisting; consumer registers their own `ISourceMapper` implementation in DI. |
| **Module-resolver hook** | Same pattern — `_moduleResolver.Resolve(parsed.PageUrl)` returns a string used for filtering. |
| **GitHub config** | `appsettings.json` block `BugFab:GitHub:{Enabled, Repository, PersonalAccessToken}` bound to `GitHubSettings`. |

These five hooks should be present (even if as `INoOpSourceMapper` defaults) so the public sketch stays a complete reference, not a partial one.

### Common pitfalls (.NET)

- **`JsonNamingPolicy.CamelCase`** — your stack default may emit `camelCase` JSON, but Bug-Fab uses `snake_case` over the wire. Configure `JsonSerializerOptions` with `PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower` (or use `[JsonPropertyName]` attributes on every DTO field).
- **Silent severity coercion.** Hand-rolled .NET implementations frequently include a line like `var severity = ValidSeverities.Contains(value) ? value : "medium";`. **Bug-Fab forbids this.** Reject with `422` instead.
- **Lock-around-GitHub-sync.** If you wrap the entire intake in a database transaction, a slow GitHub PATCH will hold the lock. Release the transaction first, then make the HTTP call, then update the row in a second short transaction with the GitHub URL.
- **Top-level vs `lifecycle.status` duplication.** Some implementations store `Status` as a top-level column AND inside the lifecycle audit log. Pick one. Bug-Fab's recommendation: top-level `status` for fast filtering; `lifecycle` is the **append-only audit trail**. Do not treat them as redundant — they carry different meaning.

---

## Express / Node.js

A minimal Express middleware that mounts the protocol endpoints, persists to Postgres via `pg`, and uses `multer` for multipart parsing.

### Routing

```javascript
import express from 'express';
import multer from 'multer';
import { Pool } from 'pg';
import fetch from 'node-fetch';

const upload = multer({
  limits: { fileSize: 10 * 1024 * 1024 },  // 10 MiB
  fileFilter: (req, file, cb) => {
    if (file.mimetype !== 'image/png') {
      return cb(new Error('unsupported_media_type'), false);
    }
    cb(null, true);
  }
});

export function bugFabRouter({ pool, screenshotDir, github }) {
  const router = express.Router();
  const intakeRouter = express.Router();
  const viewerRouter = express.Router();

  intakeRouter.post('/bug-reports',
    upload.single('screenshot'),
    async (req, res) => { /* see below */ });

  viewerRouter.get('/reports', async (req, res) => { /* ... */ });
  viewerRouter.get('/reports/:id', async (req, res) => { /* ... */ });
  viewerRouter.get('/reports/:id/screenshot', async (req, res) => { /* ... */ });
  viewerRouter.put('/reports/:id/status', express.json(), async (req, res) => { /* ... */ });
  viewerRouter.delete('/reports/:id', async (req, res) => { /* ... */ });
  viewerRouter.post('/bulk-close-fixed', async (req, res) => { /* ... */ });
  viewerRouter.post('/bulk-archive-closed', async (req, res) => { /* ... */ });

  return { intakeRouter, viewerRouter };
}
```

### Intake handler

```javascript
async function handleIntake(req, res) {
  if (!req.body.metadata || !req.file) {
    return res.status(400).json({
      error: 'validation_error',
      detail: 'metadata and screenshot are both required'
    });
  }

  let metadata;
  try {
    metadata = JSON.parse(req.body.metadata);
  } catch (e) {
    return res.status(400).json({
      error: 'validation_error',
      detail: `metadata is not valid JSON: ${e.message}`
    });
  }

  if (metadata.protocol_version !== '0.1') {
    return res.status(400).json({ error: 'unsupported_protocol_version' });
  }

  const validSeverities = ['low', 'medium', 'high', 'critical'];
  if (metadata.severity && !validSeverities.includes(metadata.severity)) {
    return res.status(422).json({
      error: 'schema_error',
      detail: `severity must be one of: ${validSeverities.join(', ')}`
    });
  }

  const required = ['title', 'description', 'page', 'client_ts'];
  for (const field of required) {
    if (!metadata[field]) {
      return res.status(422).json({
        error: 'schema_error',
        detail: `${field} is required`
      });
    }
  }

  // Source of truth for User-Agent — request header, not the client-supplied value.
  const serverUserAgent = req.headers['user-agent'] ?? '';
  const clientUserAgent = metadata.client_reported_user_agent ?? null;

  const id = await assignId(pool);
  const screenshotPath = path.join(screenshotDir, `${id}.png`);
  await fs.writeFile(screenshotPath, req.file.buffer);

  const insertSQL = `
    INSERT INTO bug_reports (
      id, received_at, protocol_version, title, description, severity, status,
      environment, page_url, user_agent_server, user_agent_client,
      metadata_json, screenshot_path
    ) VALUES ($1, NOW(), $2, $3, $4, $5, 'open', $6, $7, $8, $9, $10, $11)
    RETURNING id, received_at;
  `;
  const result = await pool.query(insertSQL, [
    id, '0.1', metadata.title, metadata.description,
    metadata.severity, metadata.environment, metadata.page.url,
    serverUserAgent, clientUserAgent,
    JSON.stringify(metadata), screenshotPath
  ]);

  // Append lifecycle entry for `created`.
  await pool.query(
    `INSERT INTO bug_report_lifecycle (bug_report_id, action, at) VALUES ($1, 'created', NOW())`,
    [id]
  );

  // Best-effort GitHub sync — never blocks success.
  let githubIssueUrl = null;
  try {
    githubIssueUrl = await syncGitHub({ id, title: metadata.title, ... });
    if (githubIssueUrl) {
      await pool.query(`UPDATE bug_reports SET github_issue_url = $1 WHERE id = $2`,
        [githubIssueUrl, id]);
    }
  } catch (e) {
    console.warn(`GitHub sync failed for ${id}:`, e.message);
  }

  return res.status(201).json({
    id,
    received_at: result.rows[0].received_at.toISOString(),
    stored_at: `file://${screenshotPath}`,
    github_issue_url: githubIssueUrl
  });
}
```

### Common pitfalls (Express)

- **`multer` error handling.** A `fileFilter` rejection surfaces as an `Error` thrown into Express's error middleware, not a clean `415`. Wire up an error handler that translates `unsupported_media_type` errors into the documented response shape.
- **`multer` size limits.** `multer` rejects oversized files mid-stream with a `LIMIT_FILE_SIZE` error. Translate this into `413 payload_too_large`, including `limit_bytes`.
- **JSON parsing for `PUT /status`.** Default Express does not parse JSON bodies. Mount `express.json()` per-route on the status endpoint (as shown above), not globally — globally enabling it conflicts with `multer` on the intake route.
- **`fetch` for GitHub.** `node-fetch` and Node 18+ native `fetch` differ in how they surface non-2xx responses. Always check `response.ok` explicitly; do not rely on a thrown error.
- **Snake_case JSON.** Express does not enforce a naming convention. Use `JSON.stringify` with object keys exactly as documented in the protocol — do not convert.

---

## Fastify (TypeScript, Fastify ≥ 5)

Fastify's plugin system makes Bug-Fab a natural drop-in: register a single plugin under two URL prefixes (intake + viewer) and the protocol's eight endpoints become wired automatically. The sketch below was validated against TKR's first integration consumer (Fastify 5 + Next.js + PostgreSQL + PM2).

### Required peer dependencies

```json
{
  "dependencies": { "fastify-plugin": "^5.0.0" },
  "peerDependencies": {
    "fastify":            ">=5.0.0",
    "@fastify/multipart": ">=10.0.0"
  }
}
```

`fastify-plugin` is required because the plugin needs to break Fastify's default encapsulation — auth hooks added at the parent scope must fire for the plugin's routes too.

### Multipart registration (do this first, exactly once)

```typescript
import multipart from '@fastify/multipart'

await app.register(multipart, {
  limits: { fileSize: 11 * 1024 * 1024 },  // 11 MiB total-body cap per PROTOCOL.md
})
```

Register `@fastify/multipart` **before** the Bug-Fab plugin. Set the size limit at the registration level — checking inside the route handler is too late, Fastify will have already buffered the bytes and rejected larger ones with its own error envelope.

If the host Fastify app already registers `@fastify/multipart` for unrelated routes (e.g., other file uploads) with a generous larger limit, do NOT re-register it. The existing larger limit covers Bug-Fab's 11 MiB.

### Plugin shape

```typescript
import type { FastifyInstance } from 'fastify'
import fp from 'fastify-plugin'

interface BugFabPluginOptions {
  storage:       IStorage          // required — see IStorage interface below
  submitPrefix?: string            // default: "/api"
  viewerPrefix?: string            // default: "/admin/bug-reports" — MUST be non-empty
  github?: { enabled: boolean; pat: string; repo: string; apiBase?: string }
  rateLimit?: { enabled: boolean; maxRequests: number; windowMs: number }
}

async function bugFabPlugin(fastify: FastifyInstance, opts: BugFabPluginOptions) {
  if (!opts.storage) throw new Error('[bug-fab] opts.storage is required')

  const submitPrefix = opts.submitPrefix ?? '/api'
  const viewerPrefix = opts.viewerPrefix ?? '/admin/bug-reports'

  // Enforce the viewer mount-prefix invariant (PROTOCOL.md §Viewer mount-prefix note).
  if (!viewerPrefix || viewerPrefix === '/') {
    throw new Error('[bug-fab] viewerPrefix must be non-empty and non-root.')
  }

  await fastify.register(async sub => {
    await registerSubmitRoutes(sub, opts.storage, opts)
  }, { prefix: submitPrefix })

  await fastify.register(async sub => {
    await registerViewerRoutes(sub, opts.storage)
  }, { prefix: viewerPrefix })
}

export const bugFab = fp(bugFabPlugin, {
  fastify: '>=5.0.0',
  name:    'fastify-bug-fab',
})
```

### Intake route — multipart parsing the Fastify way

```typescript
import type { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify'

async function registerSubmitRoutes(
  fastify: FastifyInstance,
  storage: IStorage,
  opts:    BugFabPluginOptions,
) {
  fastify.post('/bug-reports', async (req, reply) => {
    let metadataRaw: string | undefined
    let screenshotBuf: Buffer | undefined
    let screenshotType: string | undefined

    // request.parts() is an async iterator; iterate ONCE and capture each field.
    for await (const part of req.parts()) {
      if (part.type === 'field' && part.fieldname === 'metadata') {
        metadataRaw = part.value as string
      } else if (part.type === 'file' && part.fieldname === 'screenshot') {
        screenshotBuf  = await part.toBuffer()
        screenshotType = part.mimetype
      }
    }

    if (!metadataRaw || !screenshotBuf) {
      return reply.code(400).send({ error: 'validation_error',
        detail: 'metadata and screenshot multipart fields are both required' })
    }

    // PNG-only — magic-byte check, do not trust Content-Type alone.
    const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
    if (!screenshotBuf.subarray(0, 8).equals(PNG_MAGIC)) {
      return reply.code(415).send({ error: 'unsupported_media_type',
        detail: 'screenshot must be PNG' })
    }

    // Validate via your schema validator (zod / ajv / valibot).
    // Rejects unknown protocol_version → 400 unsupported_protocol_version.
    // Rejects unknown severity → 422 schema_error.
    let metadata: ValidatedSubmission
    try {
      metadata = validateSubmission(JSON.parse(metadataRaw))
    } catch (err) {
      // Distinguish the two error classes with explicit codes.
      if (err.kind === 'unsupported_protocol_version') {
        return reply.code(400).send({ error: 'unsupported_protocol_version',
          detail: err.message })
      }
      return reply.code(422).send({ error: 'schema_error', detail: err.message })
    }

    const id = await storage.saveReport({
      ...metadata,
      server_user_agent: req.headers['user-agent'] ?? '',
    }, screenshotBuf)

    // Best-effort GitHub sync. Failure must not roll back the local save.
    let githubIssueUrl: string | null = null
    if (opts.github?.enabled) {
      try {
        const result = await createGitHubIssue(opts.github, { id, ...metadata })
        if (result) {
          githubIssueUrl = result.issueUrl
          // Post-save update — see "What every adapter MUST implement" § GitHub sync.
          if (typeof storage.setGitHubIssue === 'function') {
            await storage.setGitHubIssue(id, result.issueUrl, result.issueNumber)
          }
        }
      } catch (err) { fastify.log.warn({ err }, '[bug-fab] github sync failed') }
    }

    return reply.code(201).send({
      id,
      received_at:      new Date().toISOString(),
      stored_at:        `storage://bug-reports/${id}`,
      github_issue_url: githubIssueUrl,
    })
  })
}
```

### Storage interface

```typescript
interface IStorage {
  saveReport(metadata: StoredMetadata, screenshotBytes: Buffer): Promise<string>  // returns id
  getReport(id: string): Promise<BugReportDetail | null>
  listReports(filters: ListFilters, page: number, pageSize: number):
    Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }>
  getScreenshotPath(id: string): Promise<string | null>
  updateStatus(id: string, status: Status, by: string,
               fixCommit?: string, fixDescription?: string): Promise<BugReportDetail>
  deleteReport(id: string): Promise<void>
  archiveReport(id: string): Promise<void>
  bulkCloseFixed(): Promise<number>
  bulkArchiveClosed(): Promise<number>

  // Optional — GitHub post-save update hook. Duck-typed at call-site.
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>
}
```

### Common pitfalls (Fastify)

- **fp() is mandatory.** Without `fastify-plugin` wrapping, the plugin registers in its own encapsulation — parent-scope hooks (auth, logging, etc.) won't fire for plugin routes. Always wrap with `fp()`.
- **`@fastify/multipart` size limit at registration.** Setting `limits.fileSize` in the plugin registration is the only way to enforce the 11 MiB cap — the Fastify multipart parser buffers up to that limit and then errors. Per-route checking is too late.
- **Fastify 5 is async-only.** Fastify 5 dropped the callback-style API. Plugin registrations and route handlers must return promises. `await app.register(plugin)` and `async (req, reply) => {...}` are mandatory shapes.
- **Viewer prefix must be non-empty.** The viewer registers a `GET ''` (root) HTML list route inside its prefix. Mounting at `/` collapses with the app root; the plugin throws at startup. Use `/admin`, `/admin/bug-reports`, or similar.
- **Snake_case JSON.** Don't use Fastify's auto-CamelCase plugins on Bug-Fab routes. The protocol is snake_case across the wire; conversion breaks conformance.
- **Don't wrap responses in your app's envelope.** If your host app uses `{ data, error }` envelopes (TKR style) via a shared `ok()`/`fail()` helper, exclude Bug-Fab routes — the protocol's envelope is `{ error, detail }` for failures and bare JSON for success. Wrapping breaks the conformance suite.

---

## Next.js Route Handlers (TypeScript, Next.js ≥ 14 App Router)

Next.js Route Handlers (`app/api/.../route.ts` files) let a Next.js app expose Bug-Fab's eight endpoints **without a separate backend process**. Drop the Route Handlers in, point the static bundle at them, and the Next.js app is its own Bug-Fab adapter.

This is a real fit when:

- The app is Next.js-only (no separate Fastify / Express / Django backend you'd rather mount Bug-Fab in).
- Vercel / Cloudflare Pages / similar serverless deployment — no PM2, no long-running Fastify process.
- You want the report-storage layer co-located with your Next.js data layer (e.g., the same Drizzle / Prisma client).

### File layout

```
app/
├── api/
│   └── bug-reports/
│       └── route.ts                 ← POST /api/bug-reports (intake)
├── admin/
│   └── bug-reports/
│       ├── reports/
│       │   ├── route.ts             ← GET /admin/bug-reports/reports
│       │   └── [id]/
│       │       ├── route.ts         ← GET / DELETE /admin/bug-reports/reports/{id}
│       │       ├── status/
│       │       │   └── route.ts     ← PUT /admin/bug-reports/reports/{id}/status
│       │       └── screenshot/
│       │           └── route.ts     ← GET /admin/bug-reports/reports/{id}/screenshot
│       ├── bulk-close-fixed/
│       │   └── route.ts             ← POST
│       └── bulk-archive-closed/
│           └── route.ts             ← POST
└── layout.tsx                       ← <Script> tags load bug-fab.js
```

### Intake handler — `app/api/bug-reports/route.ts`

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { validateSubmission, isValidImageBuffer } from '@/lib/bug-fab/validation'
import { Errors } from '@/lib/bug-fab/errors'

// Disable Next.js's default body parsing — we need the raw multipart.
export const runtime = 'nodejs'   // Edge runtime cannot read large buffers

export async function POST(req: NextRequest) {
  // Native FormData parsing — Next.js 14 supports this in Route Handlers.
  let form: FormData
  try {
    form = await req.formData()
  } catch (err) {
    return NextResponse.json(
      Errors.validationError('Could not parse multipart body.'),
      { status: 400 },
    )
  }

  const metadataRaw = form.get('metadata')
  const screenshotEntry = form.get('screenshot')
  if (typeof metadataRaw !== 'string' || !(screenshotEntry instanceof File)) {
    return NextResponse.json(
      Errors.validationError('metadata and screenshot are both required'),
      { status: 400 },
    )
  }

  // 11 MiB total cap — Next.js's bodyParser.sizeLimit defaults to 1MB,
  // which is too small. Configure in next.config.js, or check here.
  const screenshotBuf = Buffer.from(await screenshotEntry.arrayBuffer())
  if (screenshotBuf.length > 10 * 1024 * 1024) {
    return NextResponse.json(
      { ...Errors.payloadTooLarge(), limit_bytes: 10 * 1024 * 1024 },
      { status: 413 },
    )
  }
  if (!isValidImageBuffer(screenshotBuf)) {
    return NextResponse.json(Errors.unsupportedMediaType(), { status: 415 })
  }

  let metadata: any
  try { metadata = JSON.parse(metadataRaw) } catch {
    return NextResponse.json(
      Errors.validationError('metadata is not valid JSON'),
      { status: 400 },
    )
  }

  const result = validateSubmission(metadata)
  if (!result.ok) {
    const first = result.errors[0] ?? ''
    if (first.startsWith('__unsupported_protocol_version__:')) {
      const v = first.split(':')[1] ?? 'missing'
      return NextResponse.json(Errors.unsupportedProtocolVersion(v), { status: 400 })
    }
    return NextResponse.json(Errors.schemaError(result.errors.join('; ')), { status: 422 })
  }

  // Server-side User-Agent — see PROTOCOL.md §User-Agent trust boundary.
  const serverUA = req.headers.get('user-agent') ?? ''
  const id = await storage.saveReport({ ...metadata, server_user_agent: serverUA }, screenshotBuf)

  return NextResponse.json({
    id,
    received_at:      new Date().toISOString(),
    stored_at:        `nextjs-route://bug-reports/${id}`,
    github_issue_url: null,
  }, { status: 201 })
}
```

Configure `next.config.js` to allow the 11 MiB body:

```javascript
module.exports = {
  experimental: {
    serverActions: { bodySizeLimit: '11mb' },
  },
  // For Pages Router consumers — App Router Route Handlers ignore this:
  api: { bodyParser: { sizeLimit: '11mb' } },
}
```

### Viewer list — `app/admin/bug-reports/reports/route.ts`

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { requireAdminSession } from '@/lib/auth'   // your existing auth helper

export async function GET(req: NextRequest) {
  await requireAdminSession(req)   // throw 401 / 403 inside if not authorized

  const { searchParams } = new URL(req.url)
  const page     = Number(searchParams.get('page') ?? '1')
  const pageSize = Number(searchParams.get('page_size') ?? '20')
  const filters  = {
    status:           searchParams.get('status')   ?? undefined,
    severity:         searchParams.get('severity') ?? undefined,
    environment:      searchParams.get('environment') ?? undefined,
    include_archived: searchParams.get('include_archived') === 'true',
  }

  const result = await storage.listReports(filters as any, page, Math.min(pageSize, 200))
  return NextResponse.json(result)
}
```

### Detail / status / delete — `app/admin/bug-reports/reports/[id]/route.ts`

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { storage } from '@/lib/bug-fab/storage'
import { requireAdminSession } from '@/lib/auth'
import { Errors } from '@/lib/bug-fab/errors'

const ID_RE = /^bug-[A-Za-z]?\d{3,}$/

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  if (!ID_RE.test(params.id)) {
    return NextResponse.json(Errors.notFound(params.id), { status: 404 })
  }
  await requireAdminSession(req)
  const report = await storage.getReport(params.id)
  if (!report) return NextResponse.json(Errors.notFound(params.id), { status: 404 })
  return NextResponse.json(report)
}

export async function DELETE(req: NextRequest, { params }: { params: { id: string } }) {
  if (!ID_RE.test(params.id)) {
    return NextResponse.json(Errors.notFound(params.id), { status: 404 })
  }
  await requireAdminSession(req)
  await storage.deleteReport(params.id)
  return new NextResponse(null, { status: 204 })
}
```

`PUT /status` lives in a sibling `[id]/status/route.ts` because Route Handlers route on filename, not on a method dispatcher inside one file.

### Screenshot serve — `app/admin/bug-reports/reports/[id]/screenshot/route.ts`

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { readFile } from 'node:fs/promises'
import { storage } from '@/lib/bug-fab/storage'
import { requireAdminSession } from '@/lib/auth'

export const runtime = 'nodejs'   // node:fs unavailable in Edge runtime

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  await requireAdminSession(req)
  const path = await storage.getScreenshotPath(params.id)
  if (!path) return new NextResponse(null, { status: 404 })
  const bytes = await readFile(path)
  return new NextResponse(bytes, {
    status:  200,
    headers: { 'Content-Type': 'image/png', 'Cache-Control': 'private, max-age=300' },
  })
}
```

### Common pitfalls (Next.js Route Handlers)

- **Edge runtime cannot do filesystem I/O.** `runtime = 'nodejs'` is mandatory on routes that touch `node:fs` (intake, screenshot serve). The default runtime in some Next.js configs is Edge.
- **Body size limit.** Next.js defaults to 1 MB. Bug-Fab needs 11 MiB. Configure in `next.config.js` (App Router via `experimental.serverActions.bodySizeLimit`, Pages Router via `api.bodyParser.sizeLimit`). If you forget, intake silently 413s with a generic error.
- **Auth on every viewer route.** Route Handlers don't share middleware the way Express does. Either factor `requireAdminSession()` into a single helper called at the top of every handler, OR use Next.js middleware (`middleware.ts`) with a path matcher for `/admin/bug-reports/*`.
- **Snake_case JSON.** Same as Fastify — don't run a casing transform on Bug-Fab routes. Check your `next.config.js` and any global response middleware.
- **Filesystem on serverless platforms.** Vercel / Cloudflare Pages serverless functions don't have a writable filesystem. If you deploy there, use S3 / R2 / Cloudflare KV for screenshots and override `IStorage.getScreenshotPath` accordingly. The `notes/` reference plugin in TKR's tree assumes a real disk; serverless deployments need an alternative storage class.
- **Static bundle hosting.** Place `bug-fab.js` and `vendor/html2canvas.min.js` in `public/bug-fab/` — Next.js serves `public/` as static assets at the URL root. The `<Script>` tag in `app/layout.tsx` references `/bug-fab/bug-fab.js`.

### Why this works without a separate Fastify process

The Route Handler pattern is functionally equivalent to mounting Bug-Fab into a backend framework — Next.js IS the backend framework here. The same `IStorage` implementation runs; the only difference is that the request lifecycle is Next.js's (Server Components, edge functions optional, etc.) instead of Fastify's. The wire protocol doesn't care.

A full Next.js-only example app belongs at `examples/nextjs-minimal/` — not yet built as of this writing. See [`docs/ADAPTERS_REGISTRY.md`](./ADAPTERS_REGISTRY.md) for status.

---

## SvelteKit

SvelteKit's `+server.ts` files are a natural fit for the protocol because they expose `RequestHandler` per HTTP method. Storage via Drizzle ORM keeps things idiomatic.

### File layout

```
src/routes/
├── api/
│   └── bug-reports/
│       └── +server.ts         # POST /api/bug-reports — intake
└── admin/
    └── reports/
        ├── +server.ts         # GET / (list)
        ├── [id]/
        │   ├── +server.ts     # GET / DELETE per id
        │   ├── status/
        │   │   └── +server.ts # PUT /status
        │   └── screenshot/
        │       └── +server.ts # GET PNG
        ├── bulk-close-fixed/
        │   └── +server.ts     # POST
        └── bulk-archive-closed/
            └── +server.ts     # POST
```

### Intake — `src/routes/api/bug-reports/+server.ts`

```typescript
import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { db } from '$lib/server/db';
import { bugReports, bugReportLifecycle } from '$lib/server/schema';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

const VALID_SEVERITIES = new Set(['low', 'medium', 'high', 'critical']);

export const POST: RequestHandler = async ({ request, getClientAddress }) => {
  const formData = await request.formData();
  const metadataRaw = formData.get('metadata');
  const screenshot = formData.get('screenshot');

  if (typeof metadataRaw !== 'string' || !(screenshot instanceof File)) {
    return json({ error: 'validation_error',
                  detail: 'metadata and screenshot are both required' },
                { status: 400 });
  }

  if (screenshot.type !== 'image/png') {
    return json({ error: 'unsupported_media_type' }, { status: 415 });
  }

  if (screenshot.size > 10 * 1024 * 1024) {
    return json({ error: 'payload_too_large', limit_bytes: 10 * 1024 * 1024 },
                { status: 413 });
  }

  let metadata;
  try {
    metadata = JSON.parse(metadataRaw);
  } catch (e) {
    return json({ error: 'validation_error', detail: String(e) }, { status: 400 });
  }

  if (metadata.protocol_version !== '0.1') {
    return json({ error: 'unsupported_protocol_version' }, { status: 400 });
  }

  if (metadata.severity && !VALID_SEVERITIES.has(metadata.severity)) {
    return json({ error: 'schema_error',
                  detail: `severity must be one of: ${[...VALID_SEVERITIES].join(', ')}` },
                { status: 422 });
  }

  // Server-side User-Agent — source of truth.
  const serverUserAgent = request.headers.get('user-agent') ?? '';

  const id = await assignNextId();
  const screenshotPath = path.join(screenshotDir, `${id}.png`);
  await writeFile(screenshotPath, Buffer.from(await screenshot.arrayBuffer()));

  const inserted = await db.insert(bugReports).values({
    id,
    receivedAt: new Date(),
    protocolVersion: '0.1',
    title: metadata.title,
    description: metadata.description,
    severity: metadata.severity ?? null,
    status: 'open',
    environment: metadata.environment ?? null,
    pageUrl: metadata.page?.url,
    userAgentServer: serverUserAgent,
    userAgentClient: metadata.client_reported_user_agent ?? null,
    metadataJson: JSON.stringify(metadata),
    screenshotPath
  }).returning();

  await db.insert(bugReportLifecycle).values({
    bugReportId: id,
    action: 'created',
    at: new Date()
  });

  return json({
    id,
    received_at: inserted[0].receivedAt.toISOString(),
    stored_at: `file://${screenshotPath}`,
    github_issue_url: null
  }, { status: 201 });
};
```

### Status update — `src/routes/admin/reports/[id]/status/+server.ts`

```typescript
import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const VALID_STATUSES = new Set(['open', 'investigating', 'fixed', 'closed']);

export const PUT: RequestHandler = async ({ params, request }) => {
  const body = await request.json();

  if (!body.status || !VALID_STATUSES.has(body.status)) {
    return json({ error: 'schema_error',
                  detail: `status must be one of: ${[...VALID_STATUSES].join(', ')}` },
                { status: 422 });
  }

  const existing = await db.query.bugReports.findFirst({
    where: eq(bugReports.id, params.id)
  });
  if (!existing) {
    return json({ error: 'not_found' }, { status: 404 });
  }

  await db.update(bugReports)
    .set({ status: body.status })
    .where(eq(bugReports.id, params.id));

  await db.insert(bugReportLifecycle).values({
    bugReportId: params.id,
    action: 'status_changed',
    at: new Date(),
    fixCommit: body.fix_commit ?? null,
    fixDescription: body.fix_description ?? null
  });

  // Refetch and return full detail (with new lifecycle entry).
  const updated = await fetchDetail(params.id);
  return json(updated);
};
```

### Common pitfalls (SvelteKit)

- **`formData()` is single-shot.** You can call it once. Buffer the screenshot via `arrayBuffer()` immediately if you need to validate it before writing to disk.
- **Drizzle camelCase vs wire snake_case.** Drizzle schemas typically use camelCase TypeScript field names mapping to snake_case columns. Make sure the JSON you serialize back to clients uses snake_case — define an explicit mapping function rather than relying on direct serialization of the Drizzle row.
- **SvelteKit's auto-CSRF.** The default CSRF check rejects cross-origin POST requests. If your intake endpoint is meant to accept submissions from arbitrary origins (typical Bug-Fab use), set `csrf.checkOrigin: false` in `svelte.config.js` for the intake route, or implement your own CSRF check.
- **`error()` vs `json()`.** SvelteKit's `error()` helper throws — it does not return a `Response`. Use `json({...}, {status: ...})` to return a structured error body, since the protocol requires `{error, detail}` shape rather than SvelteKit's default error envelope.

---

## Go (`net/http` + `chi`)

Go's `net/http` plus the lightweight [`chi`](https://github.com/go-chi/chi) router and standard library `mime/multipart` give a clean adapter implementation.

### Handler signatures

```go
package bugfab

import (
    "encoding/json"
    "io"
    "net/http"
    "os"
    "path/filepath"
    "time"

    "github.com/go-chi/chi/v5"
)

type Adapter struct {
    DB            *sql.DB
    ScreenshotDir string
    GitHub        *GitHubClient
}

func (a *Adapter) MountIntake(r chi.Router) {
    r.Post("/bug-reports", a.HandleIntake)
}

func (a *Adapter) MountViewer(r chi.Router) {
    r.Get("/reports", a.HandleList)
    r.Get("/reports/{id}", a.HandleDetail)
    r.Get("/reports/{id}/screenshot", a.HandleScreenshot)
    r.Put("/reports/{id}/status", a.HandleStatusUpdate)
    r.Delete("/reports/{id}", a.HandleDelete)
    r.Post("/bulk-close-fixed", a.HandleBulkCloseFixed)
    r.Post("/bulk-archive-closed", a.HandleBulkArchiveClosed)
}
```

### Intake handler

```go
var validSeverities = map[string]struct{}{
    "low": {}, "medium": {}, "high": {}, "critical": {},
}

func (a *Adapter) HandleIntake(w http.ResponseWriter, r *http.Request) {
    if err := r.ParseMultipartForm(11 << 20); err != nil {
        writeError(w, http.StatusBadRequest, "validation_error",
                   "could not parse multipart form: "+err.Error())
        return
    }

    metadataRaw := r.FormValue("metadata")
    if metadataRaw == "" {
        writeError(w, http.StatusBadRequest, "validation_error", "metadata is required")
        return
    }

    file, header, err := r.FormFile("screenshot")
    if err != nil {
        writeError(w, http.StatusBadRequest, "validation_error", "screenshot is required")
        return
    }
    defer file.Close()

    if header.Header.Get("Content-Type") != "image/png" {
        writeError(w, http.StatusUnsupportedMediaType, "unsupported_media_type", "")
        return
    }
    if header.Size > 10<<20 {
        writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{
            "error":       "payload_too_large",
            "limit_bytes": 10 << 20,
        })
        return
    }

    var metadata BugReportMetadata
    if err := json.Unmarshal([]byte(metadataRaw), &metadata); err != nil {
        writeError(w, http.StatusBadRequest, "validation_error",
                   "metadata is not valid JSON: "+err.Error())
        return
    }

    if metadata.ProtocolVersion != "0.1" {
        writeError(w, http.StatusBadRequest, "unsupported_protocol_version", "")
        return
    }

    if metadata.Severity != "" {
        if _, ok := validSeverities[metadata.Severity]; !ok {
            writeError(w, http.StatusUnprocessableEntity, "schema_error",
                       "severity must be one of: low, medium, high, critical")
            return
        }
    }

    // Server-side User-Agent — source of truth.
    serverUserAgent := r.Header.Get("User-Agent")

    id, err := a.assignID(r.Context())
    if err != nil {
        writeError(w, http.StatusInternalServerError, "internal_error", "")
        return
    }

    screenshotPath := filepath.Join(a.ScreenshotDir, id+".png")
    out, err := os.Create(screenshotPath)
    if err != nil {
        writeError(w, http.StatusInternalServerError, "internal_error", "")
        return
    }
    defer out.Close()
    if _, err := io.Copy(out, file); err != nil {
        writeError(w, http.StatusInternalServerError, "internal_error", "")
        return
    }

    receivedAt := time.Now().UTC()
    if err := a.persistReport(r.Context(), id, receivedAt, &metadata,
                              serverUserAgent, screenshotPath, metadataRaw); err != nil {
        writeError(w, http.StatusInternalServerError, "internal_error", "")
        return
    }

    // Best-effort GitHub sync.
    var githubURL *string
    if url, err := a.GitHub.CreateIssue(r.Context(), id, &metadata); err == nil && url != "" {
        githubURL = &url
        _ = a.updateGitHubURL(r.Context(), id, url)
    }

    writeJSON(w, http.StatusCreated, map[string]any{
        "id":               id,
        "received_at":      receivedAt.Format(time.RFC3339),
        "stored_at":        "file://" + screenshotPath,
        "github_issue_url": githubURL,
    })
}

func writeError(w http.ResponseWriter, status int, code, detail string) {
    body := map[string]any{"error": code}
    if detail != "" {
        body["detail"] = detail
    }
    writeJSON(w, status, body)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    _ = json.NewEncoder(w).Encode(body)
}
```

### Persistence sketch (PostgreSQL via `database/sql`)

```go
func (a *Adapter) persistReport(ctx context.Context, id string, receivedAt time.Time,
                                m *BugReportMetadata, serverUA, screenshotPath, raw string) error {
    tx, err := a.DB.BeginTx(ctx, nil)
    if err != nil { return err }
    defer tx.Rollback()

    _, err = tx.ExecContext(ctx, `
        INSERT INTO bug_reports (
            id, received_at, protocol_version, title, description, severity, status,
            environment, page_url, user_agent_server, user_agent_client,
            metadata_json, screenshot_path
        ) VALUES ($1, $2, '0.1', $3, $4, $5, 'open', $6, $7, $8, $9, $10, $11)
    `, id, receivedAt, m.Title, m.Description, nullable(m.Severity),
       nullable(m.Environment), m.Page.URL, serverUA,
       nullable(m.ClientReportedUserAgent), raw, screenshotPath)
    if err != nil { return err }

    _, err = tx.ExecContext(ctx, `
        INSERT INTO bug_report_lifecycle (bug_report_id, action, at)
        VALUES ($1, 'created', $2)
    `, id, receivedAt)
    if err != nil { return err }

    return tx.Commit()
}
```

### Common pitfalls (Go)

- **Empty `Content-Type` on multipart parts.** Some HTTP clients omit `Content-Type` on the screenshot part. The protocol requires `image/png`; reject the request rather than guessing. Use `header.Header.Get("Content-Type")` rather than sniffing.
- **JSON tags.** Go marshals struct fields in PascalCase by default. Add explicit `json:"snake_case_name"` tags on every wire-protocol field. Bug-Fab's protocol is snake_case across the wire.
- **Time formatting.** Use `time.RFC3339` (or `RFC3339Nano`) for all ISO 8601 strings. `time.Time.String()` returns Go's native format, which is **not** ISO 8601.
- **Multipart memory.** `r.ParseMultipartForm(11 << 20)` accepts the size limit in bytes for in-memory buffering; data above that is spilled to a temp file. Make sure your temp filesystem has space, or set a tighter cap.
- **Nullable strings.** `database/sql` does not handle empty Go strings as NULL. Wrap optional fields with `sql.NullString` (or use a helper like `nullable()` above) so missing severity/environment are stored as DB NULL rather than empty strings — this matters for `WHERE severity IS NULL` queries.

---

<!-- HONO_SECTION_START -->
## Hono (TypeScript, Hono ≥ 4)

Hono is the right adapter target when the consumer runs on an edge or non-Node JavaScript runtime — Cloudflare Workers, Bun, Deno Deploy, Vercel Edge — or wants a single ultra-light server framework that runs unchanged across all of them. Hono's `c.req.parseBody()` handles `multipart/form-data` natively across runtimes, and the `Hono` instance is itself a fetch-style handler, which makes mounting Bug-Fab's two routers trivial.

### Required peer dependencies

```json
{
  "peerDependencies": {
    "hono": ">=4.0.0"
  }
}
```

Hono ships its own multipart parser as part of `c.req.parseBody()`, so no additional middleware is needed for the intake route. Storage backend dependencies (e.g., `@aws-sdk/client-s3` for R2/S3, `@cloudflare/workers-types` for KV/D1, `node:fs` on Node/Bun) are consumer-provided via the `IStorage` implementation.

### Multipart parsing

Hono delegates body parsing to the underlying runtime's `Request.formData()` (Web Fetch standard). The convenience wrapper is `c.req.parseBody()`, which returns `Record<string, string | File>` — string for text fields, `File` for upload parts. Always type-check before accessing `.arrayBuffer()` or treating a value as a string; the multipart shape is not guaranteed.

```typescript
const body = await c.req.parseBody()
const metadataRaw = body['metadata']
const screenshotEntry = body['screenshot']

if (typeof metadataRaw !== 'string' || !(screenshotEntry instanceof File)) {
  return c.json({ error: 'validation_error',
    detail: 'metadata and screenshot multipart fields are both required' }, 400)
}
```

Body size limits are runtime-defined: Cloudflare Workers caps at 100 MiB on paid plans (much less on free), Bun and Node default to no cap, Vercel Edge caps at 4.5 MiB. The 11 MiB total-body figure from PROTOCOL.md must be enforced explicitly inside the handler when the runtime ceiling exceeds it — and the consumer must verify the runtime ceiling is at least 11 MiB.

### Plugin / route registration

Hono apps are composable: build one `Hono` instance per router, then mount each under a non-empty prefix. The viewer-prefix-must-be-non-empty constraint from PROTOCOL.md still applies — the viewer's HTML list lives at the prefix root.

```typescript
import { Hono } from 'hono'

interface BugFabOptions {
  storage:       IStorage
  submitPrefix?: string                 // default: "/api"
  viewerPrefix?: string                 // default: "/admin/bug-reports" — MUST be non-empty
  github?: { enabled: boolean; pat: string; repo: string; apiBase?: string }
}

export function mountBugFab<E extends { Bindings?: unknown; Variables?: unknown }>(
  app:  Hono<E>,
  opts: BugFabOptions,
): Hono<E> {
  if (!opts.storage) throw new Error('[bug-fab] opts.storage is required')

  const submitPrefix = opts.submitPrefix ?? '/api'
  const viewerPrefix = opts.viewerPrefix ?? '/admin/bug-reports'

  if (!viewerPrefix || viewerPrefix === '/') {
    throw new Error('[bug-fab] viewerPrefix must be non-empty and non-root.')
  }

  const intake = buildIntakeRoutes(opts)
  const viewer = buildViewerRoutes(opts.storage)

  app.route(submitPrefix, intake)
  app.route(viewerPrefix, viewer)

  // Preserve the protocol's error envelope — Hono's default 500 handler
  // emits plain text, which would corrupt {error, detail} responses.
  app.onError((err, c) => {
    console.error('[bug-fab] unhandled', err)
    return c.json({ error: 'internal_error', detail: String(err.message ?? err) }, 500)
  })

  return app
}
```

### Intake route — POST /bug-reports

```typescript
import { Hono } from 'hono'
import type {
  BugReportCreate,
  BugReportIntakeResponse,
} from '@bug-fab/protocol-types'

const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

function buildIntakeRoutes(opts: BugFabOptions): Hono {
  const intake = new Hono()

  intake.post('/bug-reports', async (c) => {
    const body = await c.req.parseBody()
    const metadataRaw = body['metadata']
    const screenshotEntry = body['screenshot']

    if (typeof metadataRaw !== 'string' || !(screenshotEntry instanceof File)) {
      return c.json({ error: 'validation_error',
        detail: 'metadata and screenshot multipart fields are both required' }, 400)
    }

    const screenshotBuf = new Uint8Array(await screenshotEntry.arrayBuffer())

    if (screenshotBuf.byteLength > 10 * 1024 * 1024) {
      return c.json({ error: 'payload_too_large',
        limit_bytes: 10 * 1024 * 1024 }, 413)
    }

    // PNG magic-byte check — do not trust the File.type / Content-Type alone.
    // The conformance suite has an explicit JPEG-rejection test.
    const head = screenshotBuf.subarray(0, 8)
    if (head.length < 8 || !head.every((b, i) => b === PNG_MAGIC[i])) {
      return c.json({ error: 'unsupported_media_type',
        detail: 'screenshot must be PNG' }, 415)
    }

    let parsed: unknown
    try { parsed = JSON.parse(metadataRaw) } catch (err) {
      return c.json({ error: 'validation_error',
        detail: `metadata is not valid JSON: ${(err as Error).message}` }, 400)
    }

    // Validate via your schema validator (zod / valibot / ajv) against the
    // wire-protocol shape (snake_case BugReportCreate). The validator MUST:
    //   - reject unknown protocol_version → 400 unsupported_protocol_version
    //   - reject missing protocol_version / title / client_ts → 422 schema_error
    //   - reject unknown severity / report_type → 422 schema_error
    //   - reject reporter sub-fields > 256 chars → 422 schema_error
    let metadata: BugReportCreate
    try {
      metadata = validateSubmission(parsed)
    } catch (err: any) {
      if (err.kind === 'unsupported_protocol_version') {
        return c.json({ error: 'unsupported_protocol_version',
          detail: err.message }, 400)
      }
      return c.json({ error: 'schema_error', detail: err.message }, 422)
    }

    // Server-captured User-Agent — source of truth (PROTOCOL.md §User-Agent
    // trust boundary). The client-supplied context.user_agent is preserved
    // separately as `client_reported_user_agent`.
    const serverUA = c.req.header('user-agent') ?? ''

    const id = await opts.storage.saveReport({
      ...metadata,
      server_user_agent: serverUA,
    }, screenshotBuf)

    // Best-effort GitHub sync — failure logs but never breaks intake.
    let githubIssueUrl: string | null = null
    if (opts.github?.enabled) {
      try {
        const result = await createGitHubIssue(opts.github, { id, ...metadata })
        if (result) {
          githubIssueUrl = result.issueUrl
          if (typeof opts.storage.setGitHubIssue === 'function') {
            await opts.storage.setGitHubIssue(id, result.issueUrl, result.issueNumber)
          }
        }
      } catch (err) { console.warn('[bug-fab] github sync failed', err) }
    }

    // Minimal envelope — NEVER echo title/description/severity/etc.
    const response: BugReportIntakeResponse = {
      id,
      received_at:      new Date().toISOString(),
      stored_at:        `storage://bug-reports/${id}`,
      github_issue_url: githubIssueUrl,
    }
    return c.json(response, 201)
  })

  return intake
}
```

### Storage interface

```typescript
interface IStorage {
  saveReport(metadata: StoredMetadata, screenshotBytes: Uint8Array): Promise<string>  // returns id
  getReport(id: string): Promise<BugReportDetail | null>
  listReports(filters: ListFilters, page: number, pageSize: number):
    Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }>
  getScreenshotBytes(id: string): Promise<Uint8Array | null>
  updateStatus(id: string, status: Status, by: string,
               fixCommit?: string, fixDescription?: string): Promise<BugReportDetail>
  deleteReport(id: string): Promise<void>
  archiveReport(id: string): Promise<void>
  bulkCloseFixed(): Promise<number>
  bulkArchiveClosed(): Promise<number>

  // Optional — GitHub post-save update hook. Duck-typed at call-site.
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>
}
```

The viewer router fetches PNG bytes via `getScreenshotBytes` rather than a filesystem path — edge runtimes have no `node:fs`, so the abstraction returns bytes directly and lets the storage class decide where they came from (R2 object, KV value, local disk read on Bun/Node, etc.).

### Common pitfalls (Hono)

- **Edge runtime cannot do filesystem I/O.** If the consumer deploys to Cloudflare Workers, Vercel Edge, or Deno Deploy, the storage backend MUST NOT use `node:fs` — those imports fail at deploy time. Use R2 / S3 / KV / D1 / Durable Objects instead. Bun and Node deployments can use the filesystem-backed storage, but the same `IStorage` interface should work both ways.
- **Body size limit is runtime-defined.** Hono delegates to the runtime's `Request.formData()`. Cloudflare Workers (free plan), Vercel Edge (4.5 MiB), and Deno Deploy each impose their own ceilings — verify the deployment platform's cap is at least 11 MiB or the screenshot upload silently fails before reaching the handler. Bun and Node have no default cap; the 10 MiB screenshot / 11 MiB total checks inside the handler are authoritative there.
- **`c.req.parseBody()` returns `Record<string, string | File>`.** Don't assume `body['screenshot']` is a `File` without an `instanceof File` check; if the part is missing or duplicated, the value can be a string or an array. Multiple parts with the same name return the *last* value by default — pass `{ all: true }` if you need them all, but Bug-Fab's protocol expects one of each.
- **`app.onError(...)` is mandatory to preserve the protocol error envelope.** Hono's default error path emits plain text (`Internal Server Error`), which breaks the conformance suite's `{error, detail}` JSON expectation. Register an `onError` handler at mount time that returns `c.json({error: 'internal_error', detail}, 500)`.
- **Snake_case JSON over the wire.** Don't apply Hono camelCase / serialization middleware to Bug-Fab routes. The protocol is snake_case; converting breaks consumers and the conformance suite.
- **Don't return `BugReportDetail` from intake.** The 201 envelope is `{id, received_at, stored_at, github_issue_url}` only — echoing user-submitted free text in the intake response leaks PII into reverse-proxy logs and browser network panels (PROTOCOL.md §Response — 201 Created). Clients that want detail follow up with `GET /reports/{id}`.
- **Viewer prefix must be non-empty.** The viewer's HTML list page resolves at the empty path within its prefix. Mounting at `/` collides with the host app's root — the plugin throws at startup. Use `/admin`, `/admin/bug-reports`, or similar.

---

<!-- NESTJS_SECTION_START -->
## NestJS (TypeScript, NestJS ≥ 10)

NestJS is an opinionated TypeScript server framework built around modules, decorators, and dependency injection. Bug-Fab fits cleanly into a single `BugFabModule` with two controllers (intake + viewer), a storage service implementing `IStorage`, class-validator DTOs, and a custom exception filter that remaps NestJS's default error envelope to the protocol's `{error, detail}` shape. NestJS supports both Express and Fastify as the underlying HTTP adapter; the sketch below targets `@nestjs/platform-fastify` (recommended for size + multipart speed) and notes the Express delta inline.

### Required dependencies

```json
{
  "dependencies": {
    "@nestjs/common":           "^10.0.0",
    "@nestjs/core":             "^10.0.0",
    "@nestjs/platform-fastify": "^10.0.0",
    "@fastify/multipart":       "^10.0.0",
    "class-validator":          "^0.14.0",
    "class-transformer":        "^0.5.0",
    "reflect-metadata":         "^0.2.0",
    "rxjs":                     "^7.8.0"
  }
}
```

If you prefer Express underneath, swap `@nestjs/platform-fastify` + `@fastify/multipart` for `@nestjs/platform-express` + `multer` (already a NestJS peer). Pick one — do not pull both.

### Module layout

```
src/bug-fab/
├── bug-fab.module.ts                 (registers everything below)
├── bug-fab.service.ts                (IStorage implementation)
├── bug-fab-submit.controller.ts      (POST /api/bug-reports)
├── bug-fab-viewer.controller.ts      (GET/PUT/DELETE /admin/bug-reports/*)
├── dto/
│   ├── bug-report-create.dto.ts      (class-validator on submission)
│   ├── bug-report-status-update.dto.ts
│   └── reporter.dto.ts
├── interfaces/
│   └── storage.interface.ts          (IStorage)
└── filters/
    └── bug-fab-exception.filter.ts   (custom @Catch → protocol envelope)
```

### DTOs (class-validator)

```typescript
// dto/reporter.dto.ts
import { IsOptional, IsString, MaxLength } from 'class-validator';

export class ReporterDto {
  @IsOptional() @IsString() @MaxLength(256) name?: string;
  @IsOptional() @IsString() @MaxLength(256) email?: string;
  @IsOptional() @IsString() @MaxLength(256) user_id?: string;
}

// dto/bug-report-create.dto.ts
import { IsArray, IsEnum, IsIn, IsObject, IsOptional, IsString,
         Length, MinLength, ValidateNested } from 'class-validator';
import { Type } from 'class-transformer';
import { ReporterDto } from './reporter.dto';

export class BugReportCreateDto {
  @IsIn(['0.1'], { message: 'unsupported_protocol_version' })
  protocol_version!: '0.1';

  @IsString() @Length(1, 200) title!: string;

  @IsString() @MinLength(1) client_ts!: string;

  @IsOptional() @IsEnum(['bug', 'feature_request'])
  report_type?: 'bug' | 'feature_request';

  @IsOptional() @IsString() description?: string;
  @IsOptional() @IsString() expected_behavior?: string;

  @IsOptional() @IsEnum(['low', 'medium', 'high', 'critical'])
  severity?: 'low' | 'medium' | 'high' | 'critical';

  @IsOptional() @IsArray() @IsString({ each: true }) tags?: string[];

  @IsOptional() @ValidateNested() @Type(() => ReporterDto)
  reporter?: ReporterDto;

  @IsOptional() @IsObject() context?: Record<string, unknown>;
}
```

`ValidationPipe` is applied at the controller level (NOT globally) so it does not also intercept the consumer's other routes:

```typescript
@UsePipes(new ValidationPipe({
  whitelist: true,
  forbidNonWhitelisted: false,         // context allows extra keys per protocol
  transform: false,                    // CRITICAL — see pitfalls below
}))
```

### Submit controller — `POST /api/bug-reports`

```typescript
import { Controller, Post, Req, UseFilters, HttpCode } from '@nestjs/common';
import { FastifyRequest } from 'fastify';

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

@Controller('api')
@UseFilters(BugFabExceptionFilter)
export class BugFabSubmitController {
  constructor(private readonly service: BugFabService) {}

  @Post('bug-reports')
  @HttpCode(201)
  async submit(@Req() req: FastifyRequest) {
    // Iterate parts ONCE — fastify multipart is a single-shot async iterator.
    let metadataRaw: string | undefined;
    let screenshotBuf: Buffer | undefined;
    for await (const part of (req as any).parts()) {
      if (part.type === 'field' && part.fieldname === 'metadata') metadataRaw = part.value;
      else if (part.type === 'file' && part.fieldname === 'screenshot') screenshotBuf = await part.toBuffer();
    }
    if (!metadataRaw || !screenshotBuf) {
      throw new BugFabError('validation_error', 400,
        'metadata and screenshot multipart fields are both required');
    }
    // Magic-byte PNG check — do NOT trust multipart mimetype.
    if (!screenshotBuf.subarray(0, 8).equals(PNG_MAGIC)) {
      throw new BugFabError('unsupported_media_type', 415, 'screenshot must be PNG');
    }
    if (screenshotBuf.length > 10 * 1024 * 1024) {
      throw new BugFabError('payload_too_large', 413,
        'screenshot exceeds 10 MiB cap', { limit_bytes: 10 * 1024 * 1024 });
    }
    let metadata: any;
    try { metadata = JSON.parse(metadataRaw); }
    catch (e) { throw new BugFabError('validation_error', 400,
      `metadata is not valid JSON: ${(e as Error).message}`); }

    // Run class-validator manually — JSON is already parsed.
    // Maps unsupported_protocol_version → 400, other failures → 422 schema_error.
    const dto = await validateMetadata(metadata);

    const serverUA = req.headers['user-agent'] ?? '';
    const result = await this.service.saveReport(dto, screenshotBuf, serverUA);

    // Minimal envelope — never echo user-submitted text in 201 body.
    return { id: result.id, received_at: result.received_at,
             stored_at: result.stored_at, github_issue_url: result.github_issue_url };
  }
}
```

### Viewer controller

```typescript
@Controller('admin/bug-reports')
@UseFilters(BugFabExceptionFilter)
@UseGuards(AuthGuard('jwt'))                // consumer-supplied
export class BugFabViewerController {
  constructor(private readonly service: BugFabService) {}

  @Get('')                                  // HTML list page (non-empty prefix required)
  list(@Res() reply: FastifyReply) { /* render or redirect to /reports */ }

  @Get('reports')
  async listReports(@Query() filters: ListFiltersDto) {
    return this.service.listReports(filters);
  }

  @Get('reports/:id')
  async detail(@Param('id') id: string) {
    const report = await this.service.getReport(id);
    if (!report) throw new BugFabError('not_found', 404, id);
    return report;
  }

  @Get('reports/:id/screenshot')
  async screenshot(@Param('id') id: string, @Res() reply: FastifyReply) {
    const path = await this.service.getScreenshotPath(id);
    if (!path) throw new BugFabError('not_found', 404, id);
    reply.type('image/png').send(await readFile(path));
  }

  @Put('reports/:id/status')
  @UsePipes(new ValidationPipe({ whitelist: true, transform: false }))
  async updateStatus(@Param('id') id: string,
                     @Body() body: BugReportStatusUpdateDto) {
    return this.service.updateStatus(id, body);
  }

  @Delete('reports/:id')
  @HttpCode(204)
  async delete(@Param('id') id: string) { await this.service.deleteReport(id); }

  @Post('bulk-close-fixed')
  async bulkClose() { return { closed: await this.service.bulkCloseFixed() }; }

  @Post('bulk-archive-closed')
  async bulkArchive() { return { archived: await this.service.bulkArchiveClosed() }; }
}
```

### Storage service

`BugFabService` implements the 9-method `IStorage` interface plus the optional `setGitHubIssue` post-save hook. TypeORM is the recommended persistence layer (NestJS docs default there) — Prisma works equally well if the consumer already uses it.

```typescript
// interfaces/storage.interface.ts
export interface IStorage {
  saveReport(metadata: BugReportCreateDto, screenshot: Buffer, serverUA: string):
    Promise<BugReportIntakeResponse>;
  getReport(id: string): Promise<BugReportDetail | null>;
  listReports(filters: ListFilters): Promise<BugReportListResponse>;
  getScreenshotPath(id: string): Promise<string | null>;
  updateStatus(id: string, body: BugReportStatusUpdateDto): Promise<BugReportDetail>;
  deleteReport(id: string): Promise<void>;
  archiveReport(id: string): Promise<void>;
  bulkCloseFixed(): Promise<number>;
  bulkArchiveClosed(): Promise<number>;
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>;
}

// bug-fab.service.ts (sketch — TypeORM repos injected; Prisma client equivalent)
@Injectable()
export class BugFabService implements IStorage {
  constructor(
    @InjectRepository(BugReport) private readonly reports: Repository<BugReport>,
    @InjectRepository(BugReportLifecycle) private readonly lifecycle: Repository<BugReportLifecycle>,
    private readonly github: GitHubIssueService,
  ) {}

  async saveReport(metadata, screenshot, serverUA) {
    const id = await this.assignId();
    const screenshotPath = path.join(this.dataPath, `${id}.png`);
    await writeFile(screenshotPath, screenshot);

    const report = await this.reports.save({
      id, title: metadata.title, severity: metadata.severity ?? 'medium',
      status: 'open', protocol_version: '0.1',
      user_agent_server: serverUA,
      user_agent_client: metadata.context?.user_agent ?? '',
      metadata_json: JSON.stringify(metadata),
      screenshot_path: screenshotPath, received_at: new Date(),
    });

    // Append-only lifecycle entry.
    await this.lifecycle.save({ bug_report_id: id, action: 'created', at: new Date(),
      by: metadata.reporter?.user_id || metadata.reporter?.email || 'anonymous' });

    // Best-effort GitHub sync — never fail the intake response.
    let githubIssueUrl: string | null = null;
    try {
      const issue = await this.github.createIssue(report);
      if (issue) { githubIssueUrl = issue.url;
                   await this.setGitHubIssue(id, issue.url, issue.number); }
    } catch (err) { this.logger.warn(`github sync failed for ${id}: ${err}`); }

    return { id, received_at: report.received_at.toISOString(),
             stored_at: `file://${screenshotPath}`, github_issue_url: githubIssueUrl };
  }

  async setGitHubIssue(id, url, number) {
    await this.reports.update(id, { github_issue_url: url, github_issue_number: number });
  }
  // ... remaining IStorage methods (getReport, listReports, updateStatus, etc.)
}
```

### Custom exception filter for protocol error envelopes

NestJS's default exception responses are `{statusCode, message, error}` — the wrong shape. Translate to the protocol envelope:

```typescript
// filters/bug-fab-exception.filter.ts
import { ArgumentsHost, Catch, ExceptionFilter, HttpException } from '@nestjs/common';

export class BugFabError extends HttpException {
  constructor(public code: string, status: number,
              public detail: string | unknown[],
              public extras: Record<string, unknown> = {}) {
    super({ error: code, detail }, status);
  }
}

@Catch()
export class BugFabExceptionFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost) {
    const reply = host.switchToHttp().getResponse();

    if (exception instanceof BugFabError) {
      return reply.code(exception.getStatus())
        .send({ error: exception.code, detail: exception.detail, ...exception.extras });
    }

    // class-validator failures arrive as BadRequestException with array `message`.
    if (exception instanceof HttpException) {
      const resp = exception.getResponse() as any;
      const detail = Array.isArray(resp?.message) ? resp.message : (resp?.message ?? resp);
      const code = exception.getStatus() === 422 ? 'schema_error' : 'validation_error';
      return reply.code(exception.getStatus()).send({ error: code, detail });
    }

    return reply.code(500).send({ error: 'internal_error', detail: 'unhandled server exception' });
  }
}
```

### Auth — Guard

Apply `@UseGuards(...)` at the **viewer controller** only. Intake stays unguarded so end users can submit reports without an auth token:

```typescript
@Controller('admin/bug-reports')
@UseGuards(AuthGuard('jwt'))               // viewer-only
export class BugFabViewerController { ... }

// Intake controller is deliberately UNGUARDED — apply the guard
// at the consumer's mount point if intake should be authenticated.
```

The consumer can swap `AuthGuard('jwt')` for `AuthGuard('local')`, a custom `RolesGuard`, or any NestJS guard; the protocol does not constrain the choice.

### Common pitfalls (NestJS)

- **Global `ClassSerializerInterceptor`.** If the host app registers it globally, it auto-converts response objects to camelCase, which breaks the snake_case wire format. Either exclude Bug-Fab routes from the interceptor or do not register it inside `BugFabModule`.
- **Default `BadRequestException` shape.** NestJS returns `{statusCode, message, error}` — wrong shape for Bug-Fab. The custom `@Catch()` filter above is required; without it, conformance fails on every validation error.
- **`@Body()` decorator with `transform: true`.** If `ValidationPipe` is constructed with `transform: true`, class-transformer silently rewrites unknown keys into the DTO and can flatten `protocol_version` → `protocolVersion` on serialization. Always pass `transform: false` on Bug-Fab routes, or use `@Expose({ name: 'snake_field' })` per property.
- **`FileInterceptor` size limit defaults too small.** Express's body-parser caps multipart at ~100 KB by default. When using `@nestjs/platform-express` + `multer`, set `limits: { fileSize: 11 * 1024 * 1024 }` explicitly on the `FileInterceptor`. With `@nestjs/platform-fastify` + `@fastify/multipart`, set `limits.fileSize` at registration time — checking inside the route handler is too late.
- **`@nestjs/platform-fastify` vs `@nestjs/platform-express`.** Multipart handling is different (fastify async iterator vs multer middleware). Pin `@fastify/multipart` for the fastify path or `multer` for the express path — never both. The sketch above uses fastify; the express path swaps the iterator for `@UseInterceptors(FileInterceptor('screenshot', { limits }))` plus `@UploadedFile()`.
- **Auth guard on the wrong scope.** Applying `@UseGuards(...)` at the module level (via a global guard) gates intake too, which breaks the public-submit pattern. Apply guards only on the viewer controller, or on the consumer's mount point — never as a module-wide default.
- **`forbidNonWhitelisted: true` on the context field.** The protocol explicitly allows extra keys inside `context` (consumer-specific diagnostics round-trip verbatim). Setting `forbidNonWhitelisted: true` rejects valid submissions. Use `whitelist: true` with `forbidNonWhitelisted: false`, or split the validation so only the top-level DTO is whitelisted.
- **TypeORM camelCase columns.** TypeORM defaults to snake_case in some configurations and camelCase in others. Pin column names with `@Column({ name: 'protocol_version' })` so the wire format and DB schema stay aligned regardless of the global naming strategy.
- **Treating lifecycle as mutable.** Some NestJS implementations use `repo.update()` on lifecycle rows. The protocol requires append-only — service-layer code must only `INSERT`, never `UPDATE` or `DELETE` on the lifecycle table.

---

<!-- DJANGO_SECTION_START -->
## Django (Django ≥ 4.2, Python ≥ 3.10)

Django covers a large slice of the Python web ecosystem — ORM-first, batteries-included, auth/admin/sessions out of the box. A Bug-Fab adapter slots in as a reusable Django app registered in `INSTALLED_APPS`, with the wire protocol mapped to plain Django views and storage backed by the ORM.

### Required dependencies

```toml
django    >= 4.2
requests  >= 2.31   # best-effort GitHub Issues sync
```

DRF is **not** a hard dependency — the sketch uses plain Django views (`JsonResponse`, `View`, `csrf_exempt`). If the host project already uses DRF, see the pitfalls section about disabling camelCase renderers per-route.

### App layout

Bug-Fab lives as a self-contained Django app. Recommended placement: `apps/bug_fab/` (or wherever the host project keeps its first-party apps), registered in `INSTALLED_APPS`.

```
apps/bug_fab/
├── apps.py                  # AppConfig — name = "apps.bug_fab"
├── models.py                # BugReport + BugReportLifecycle
├── managers.py              # BugReportManager (storage helpers)
├── views.py                 # Intake + viewer views
├── urls/intake.py           # POST /bug-reports
├── urls/viewer.py           # /reports*, /bulk-*
├── auth.py                  # AdminRequiredMixin
├── validators.py            # Severity / Status / protocol_version checks
├── github.py                # Best-effort GitHub Issues sync
└── migrations/
```

In the project's root `urls.py`, the host mounts the two routers under separate prefixes so they can be guarded by different middleware:

```python
# project/urls.py
from django.urls import include, path

urlpatterns = [
    path("api/",                include("apps.bug_fab.urls.intake")),  # open submit
    path("admin/bug-reports/",  include("apps.bug_fab.urls.viewer")),  # auth-required
]
```

### Model layer

Two models — one row per report, append-only lifecycle entries in a child table. Screenshots live on disk via `FileField`; the file path is stored, never the bytes.

```python
# apps/bug_fab/models.py
from django.db import models
from django.utils import timezone


class BugReport(models.Model):
    class ReportType(models.TextChoices):
        BUG = "bug", "Bug"
        FEATURE_REQUEST = "feature_request", "Feature request"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        FIXED = "fixed", "Fixed"
        CLOSED = "closed", "Closed"

    # Wire-protocol fields — snake_case names match the protocol exactly.
    id = models.CharField(primary_key=True, max_length=64)  # bug-NNN format
    protocol_version = models.CharField(max_length=16, default="0.1")
    title = models.CharField(max_length=200)
    report_type = models.CharField(
        max_length=32, choices=ReportType.choices, default=ReportType.BUG,
    )
    description = models.TextField(blank=True, default="")
    expected_behavior = models.TextField(blank=True, default="")
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.MEDIUM,
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True,
    )
    tags = models.JSONField(default=list, blank=True)

    # Reporter sub-fields (each capped at 256 per protocol).
    reporter_name = models.CharField(max_length=256, blank=True, default="")
    reporter_email = models.CharField(max_length=256, blank=True, default="")
    reporter_user_id = models.CharField(max_length=256, blank=True, default="")

    # Auto-captured browser context — JSON blob with extra="allow" semantics.
    context = models.JSONField(default=dict, blank=True)

    # User-Agent trust boundary — keep both, never overwrite the server one.
    server_user_agent = models.TextField(blank=True, default="")
    client_reported_user_agent = models.TextField(blank=True, default="")

    # Screenshot lives on disk. MEDIA_ROOT must be configured.
    screenshot = models.FileField(upload_to="bug_reports/%Y/%m/", max_length=512)

    # GitHub sync (best-effort).
    github_issue_url = models.URLField(blank=True, default="")
    github_issue_number = models.IntegerField(null=True, blank=True)

    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    objects = "BugReportManager()"  # see managers.py

    class Meta:
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["created_at"]),
        ]


class BugReportLifecycle(models.Model):
    """Append-only audit log. NEVER update existing rows — only insert."""
    report = models.ForeignKey(
        BugReport, on_delete=models.CASCADE, related_name="lifecycle",
    )
    action = models.CharField(max_length=32)  # created | status_changed | archived | deleted
    by = models.CharField(max_length=256, blank=True, default="anonymous")
    at = models.DateTimeField(default=timezone.now)
    fix_commit = models.CharField(max_length=512, blank=True, default="")
    fix_description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["at", "id"]
        indexes = [models.Index(fields=["report", "at"])]
```

### URL routing

Two URL conf modules — one per router — so the host project mounts them at different prefixes with different middleware. Per [`PROTOCOL.md`](./PROTOCOL.md) § Viewer mount-prefix note, the viewer prefix MUST be non-empty.

```python
# apps/bug_fab/urls/intake.py
from django.urls import path
from apps.bug_fab.views import IntakeView

urlpatterns = [
    path("bug-reports", IntakeView.as_view(), name="bug_fab_intake"),
]

# apps/bug_fab/urls/viewer.py
from django.urls import path
from apps.bug_fab import views

urlpatterns = [
    path("",                       views.report_list,      name="bug_fab_list"),
    path("reports",                views.report_list_json, name="bug_fab_list_json"),
    path("reports/<str:rid>",      views.report_detail,    name="bug_fab_detail"),
    path("reports/<str:rid>/screenshot", views.screenshot, name="bug_fab_screenshot"),
    path("reports/<str:rid>/status",     views.status_update, name="bug_fab_status"),
    path("reports/<str:rid>/delete",     views.delete_report, name="bug_fab_delete"),
    path("bulk-close-fixed",       views.bulk_close_fixed, name="bug_fab_bulk_close"),
    path("bulk-archive-closed",    views.bulk_archive_closed, name="bug_fab_bulk_archive"),
]
```

### Intake view — `POST /bug-reports`

Class-based view. Uses `@csrf_exempt` because intake commonly comes from a different origin than the Django app (the Bug-Fab frontend bundle attaches to whatever page the user is on). Returns the **minimal envelope** — never echoes user-submitted text.

```python
# apps/bug_fab/views.py
import json
from datetime import datetime, timezone as dt_timezone

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.bug_fab.models import BugReport, BugReportLifecycle
from apps.bug_fab.validators import validate_submission, ValidationError
from apps.bug_fab import github

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SCREENSHOT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB

def _err(code, detail, status):
    return JsonResponse({"error": code, "detail": detail}, status=status)


@method_decorator(csrf_exempt, name="dispatch")
class IntakeView(View):
    def post(self, request):
        # Multipart parts.
        metadata_raw = request.POST.get("metadata")
        screenshot = request.FILES.get("screenshot")
        if not metadata_raw or not screenshot:
            return _err("validation_error",
                        "metadata and screenshot are both required", 400)

        # Size cap (Django may already have rejected — see DATA_UPLOAD_* settings).
        if screenshot.size > SCREENSHOT_MAX_BYTES:
            return JsonResponse({
                "error": "payload_too_large",
                "limit_bytes": SCREENSHOT_MAX_BYTES,
            }, status=413)

        # Magic-byte PNG check. Do NOT trust Content-Type alone.
        head = screenshot.read(8)
        screenshot.seek(0)
        if head != PNG_MAGIC:
            return _err("unsupported_media_type",
                        "screenshot must be PNG", 415)

        # Parse JSON.
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError as exc:
            return _err("validation_error", f"metadata is not valid JSON: {exc}", 400)

        # Validate. Raises ValidationError with .code and .status.
        try:
            data = validate_submission(metadata)
        except ValidationError as exc:
            return _err(exc.code, exc.detail, exc.status)

        # User-Agent trust boundary — server header is source of truth.
        server_ua = request.META.get("HTTP_USER_AGENT", "")
        client_ua = data.get("context", {}).get("user_agent", "")
        received_at = datetime.now(dt_timezone.utc)

        # Atomic: insert report + initial lifecycle entry in one transaction.
        with transaction.atomic():
            report = BugReport.objects.create_with_id(
                title=data["title"],
                report_type=data.get("report_type", "bug"),
                description=data.get("description", ""),
                expected_behavior=data.get("expected_behavior", ""),
                severity=data.get("severity", "medium"),
                status="open",
                tags=data.get("tags", []),
                reporter_name=data.get("reporter", {}).get("name", ""),
                reporter_email=data.get("reporter", {}).get("email", ""),
                reporter_user_id=data.get("reporter", {}).get("user_id", ""),
                context=data.get("context", {}),
                server_user_agent=server_ua,
                client_reported_user_agent=client_ua,
                screenshot=ContentFile(screenshot.read(), name=f"{{id}}.png"),
                created_at=received_at,
                updated_at=received_at,
            )
            BugReportLifecycle.objects.create(
                report=report, action="created", by="anonymous", at=received_at,
            )

        # Best-effort GitHub sync — outside the transaction so a slow API
        # never blocks the local write. Failures log; intake still returns 201.
        issue_url = None
        try:
            link = github.create_issue(report)
            if link is not None:
                issue_url = link.url
                BugReport.objects.filter(pk=report.id).update(
                    github_issue_url=link.url, github_issue_number=link.number,
                )
        except Exception:  # pragma: no cover — log + swallow
            pass

        return JsonResponse({
            "id": report.id,
            "received_at": received_at.isoformat(),
            "stored_at": f"file://{report.screenshot.path}",
            "github_issue_url": issue_url,
        }, status=201)
```

### Storage / ORM helpers

A Django manager method bundle mirrors the `IStorage` contract used by the other adapter sketches. The Python reference adapter ships `FileStorage` and `SQLiteStorage`; the Django ORM is the natural equivalent.

```python
# apps/bug_fab/managers.py
from django.db import models, transaction
from django.utils import timezone


class BugReportManager(models.Manager):
    def create_with_id(self, **fields):
        last = self.order_by("-created_at").first()
        seq = int(last.id.rsplit("-", 1)[-1]) + 1 if last else 1
        return self.create(id=f"bug-{seq:03d}", **fields)

    def list_reports(self, *, status=None, severity=None, environment=None,
                     include_archived=False, page=1, page_size=20):
        qs = self.all()
        if not include_archived:  qs = qs.filter(archived_at__isnull=True)
        if status:                qs = qs.filter(status=status)
        if severity:              qs = qs.filter(severity=severity)
        if environment:           qs = qs.filter(context__environment=environment)
        page_size = min(page_size, 200)
        offset = (page - 1) * page_size
        return list(qs[offset:offset + page_size]), qs.count()

    def get_report(self, rid):
        return self.filter(pk=rid).first()

    @transaction.atomic
    def update_status(self, rid, *, status, by, fix_commit="", fix_description=""):
        from apps.bug_fab.models import BugReportLifecycle
        report = self.select_for_update().filter(pk=rid).first()
        if not report: return None
        report.status = status
        report.save(update_fields=["status", "updated_at"])
        BugReportLifecycle.objects.create(
            report=report, action="status_changed", by=by,
            fix_commit=fix_commit, fix_description=fix_description,
        )
        return report

    @transaction.atomic
    def archive_report(self, rid):
        return self.filter(pk=rid).update(archived_at=timezone.now())

    @transaction.atomic
    def bulk_close_fixed(self):
        return self.filter(status="fixed").update(status="closed")

    @transaction.atomic
    def bulk_archive_closed(self):
        return self.filter(status="closed", archived_at__isnull=True
                          ).update(archived_at=timezone.now())
```

### Auth — middleware-based

Django's standard auth covers Bug-Fab's needs without a separate `AuthAdapter`. The viewer routes get a `LoginRequiredMixin` (or a stricter custom check); the intake route stays open by default per the standard Bug-Fab pattern.

```python
# apps/bug_fab/auth.py
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Default viewer guard. Override is_admin() per host project's idea of admin."""
    def test_func(self):
        return self.request.user.is_staff
```

Apply per viewer route:

```python
class ReportListView(AdminRequiredMixin, View):
    def get(self, request): ...
```

If the host already mounts viewer URLs behind admin-only middleware (e.g., a tenant-aware SSO middleware applied to `/admin/`), the mixin can be omitted — protection is at the mount point. v0.1 has no per-user permission abstraction; that arrives with `AuthAdapter` in v0.2.

### Common pitfalls (Django)

- **DRF camelCase renderers.** If the host uses `djangorestframework-camel-case`, its renderer rewrites `received_at`, `protocol_version`, `client_ts` into `receivedAt`, `protocolVersion`, `clientTs` — silently breaking the wire protocol. Fix: use plain `JsonResponse` views (as above), or add `@renderer_classes([JSONRenderer])` per-view to bypass the camelCase renderer.
- **`csrf_exempt` on intake.** The Bug-Fab frontend bundle posts from the consumer page's origin without a Django CSRF token. Without `@csrf_exempt` every submission gets `403 CSRF verification failed`. Apply it to the intake view only — never to viewer mutation routes (status update, delete).
- **`MEDIA_ROOT` not configured.** `FileField` uses `MEDIA_ROOT` as its base. If unset, files land wherever the working directory points — often the project root. Set `MEDIA_ROOT = "/var/lib/<app>/storage"` (or another persistent volume); `upload_to="bug_reports/%Y/%m/"` resolves under it.
- **`request.FILES` upload size limits.** Django defaults `DATA_UPLOAD_MAX_MEMORY_SIZE` and `FILE_UPLOAD_MAX_MEMORY_SIZE` to 2.5 MB. Bug-Fab's 11 MiB cap requires raising both to `12 * 1024 * 1024`. Without this, large PNGs are rejected with a generic `RequestDataTooBig` before the intake view runs, never producing the protocol's `413 payload_too_large`.
- **Multi-process safety.** Django is multi-process safe via the database, so the model approach above scales to multi-worker / multi-container deploys without shared in-memory state. `create_with_id` should use `select_for_update()` or a DB sequence in production to avoid `bug-NNN` collisions under heavy concurrent load.
- **PostgreSQL `jsonb` vs SQLite JSON.** PostgreSQL's `jsonb` supports indexed queries on `context__environment=...` and works efficiently with `JSONField`. SQLite stores JSON as text — same ORM queries work but degrade on large tables. Production: PostgreSQL. SQLite is fine for dev / small POCs.
- **`auto_now_add` vs server-side defaults.** Avoid `auto_now_add=True` / `auto_now=True` on `created_at` / `updated_at`. Use `default=django.utils.timezone.now` so the timestamp is set explicitly inside the same `transaction.atomic()` block as the lifecycle insert. Under multi-process deploys, clock skew between workers with `auto_now_add` can produce out-of-order timestamps that confuse the audit log.

---

## Reference-implementation validation

These sketches were validated against a **.NET 8 + React reference consumer** during the Bug-Fab v0.1 design pass. The consumer was a hand-rolled bug-reporter with the same protocol shape; mapping its existing behavior onto these sketches confirmed each adapter could be built cleanly without protocol stretching.

If you build a fully-fledged adapter from one of these sketches, please file a PR linking to your repo and we'll list it in [`ROADMAP.md`](./ROADMAP.md) once it passes [conformance](./CONFORMANCE.md).
