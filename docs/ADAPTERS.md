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

## Reference-implementation validation

These sketches were validated against a **.NET 8 + React reference consumer** during the Bug-Fab v0.1 design pass. The consumer was a hand-rolled bug-reporter with the same protocol shape; mapping its existing behavior onto these sketches confirmed each adapter could be built cleanly without protocol stretching.

If you build a fully-fledged adapter from one of these sketches, please file a PR linking to your repo and we'll list it in [`ROADMAP.md`](./ROADMAP.md) once it passes [conformance](./CONFORMANCE.md).
