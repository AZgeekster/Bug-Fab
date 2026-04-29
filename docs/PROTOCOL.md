# Bug-Fab Wire Protocol — v0.1

This document is the **authoritative wire-protocol specification** for Bug-Fab. Anything that speaks HTTP can implement this protocol and interoperate with the Bug-Fab frontend bundle, the reference Python adapter, and the conformance test suite.

If you are writing a backend adapter in any language, this is the contract you are honoring.

---

## Status of this document

- **Protocol version:** `0.1`
- **Stability:** v0.1 is the first published version. Once consumers integrate, the wire protocol is contract — no breaking changes without a deprecation plan (see [Versioning](#versioning)).
- **Companion docs:** [`CONFORMANCE.md`](./CONFORMANCE.md) (how to verify your adapter), [`ADAPTERS.md`](./ADAPTERS.md) (reference sketches in non-Python stacks).

---

## Versioning

Bug-Fab uses an **independent protocol version** that is decoupled from the Python package version. Frontend clients send `protocol_version` in the metadata of every submission so adapters can branch on it.

### Rules

| Rule | Behavior |
|------|----------|
| Current version | `"0.1"` (string, not a number — preserve quotes in JSON). |
| Required field | `protocol_version` MUST be present in submission metadata. Adapters MUST reject missing values with `400 Bad Request`. |
| Unknown version | Adapters MUST reject unknown versions with `400 Bad Request` and `error: "unsupported_protocol_version"`. |
| Additive changes | Adding optional fields, optional endpoints, or new error codes does NOT bump the version. |
| Breaking changes | Renaming or removing fields, changing required-vs-optional, changing response shapes, or tightening enum values bumps the version (e.g., `"0.2"`, `"1.0"`) and MUST ship with a documented deprecation window. |
| Storage round-trip | Stored reports MUST record the `protocol_version` they were submitted under so older reports remain renderable after a bump. |

### Deprecated-values rule (CRITICAL)

> **Adapters MUST accept deprecated enum values on read indefinitely. Adapters MAY reject deprecated values on write.**

This rule exists because file-based and SQL-based stores can outlive several protocol revisions. If an adapter rejects every value it does not currently know how to write, every protocol bump silently breaks reads of historical data.

Concrete example: if a future release retires the `investigating` status value in favor of something else, adapters built against that future protocol still MUST be able to read and display reports stored with `status: "investigating"`. They simply MAY refuse to accept new submissions or status updates with that value.

Conformance suites enforce this — see [`CONFORMANCE.md`](./CONFORMANCE.md).

---

## Transport

| Aspect | Value |
|--------|-------|
| Protocol | HTTP/1.1 or HTTP/2 |
| TLS | Recommended for any non-localhost deployment. The protocol itself does not require TLS. |
| Methods | `GET`, `POST`, `PUT`, `DELETE` |
| Auth | Adapter-defined. v0.1 ships no auth abstraction; consumers protect endpoints by mounting Bug-Fab routers behind their existing auth middleware. v0.2 will introduce an `AuthAdapter` ABC. |
| Field naming | `snake_case` across the wire, in both metadata and JSON response bodies. Clients convert to local conventions on receipt. |

---

## Endpoints

Bug-Fab v0.1 defines **eight endpoints**. Paths below are the canonical defaults; consumers may mount each router under any URL prefix.

| Method | Path | Content-Type (req) | Content-Type (resp) | Purpose |
|--------|------|--------------------|---------------------|---------|
| `POST` | `/bug-reports` | `multipart/form-data` | `application/json` | Submit a new report (intake). |
| `GET` | `/reports` | — | `application/json` | List reports with optional filters. |
| `GET` | `/reports/{id}` | — | `application/json` | Fetch a single report's full detail. |
| `GET` | `/reports/{id}/screenshot` | — | `image/png` | Fetch the raw screenshot blob. |
| `PUT` | `/reports/{id}/status` | `application/json` | `application/json` | Update a report's status; appends to lifecycle audit log. |
| `DELETE` | `/reports/{id}` | — | — (`204`) | Permanently delete a report. |
| `POST` | `/bulk-close-fixed` | — | `application/json` | Close all reports currently in `fixed` status. |
| `POST` | `/bulk-archive-closed` | — | `application/json` | Move all `closed` reports to the archive. |

The first endpoint (`POST /bug-reports`) is the **intake router**. The remaining seven are the **viewer router**. Adapters SHOULD ship them as separate mountable units so consumers can apply different auth middleware to intake vs administration. See [Auth](#auth--mount-point-delegation).

---

## `POST /bug-reports` — submit a report

The intake endpoint. Accepts a multipart body with two parts: serialized JSON metadata and a PNG screenshot.

### Request

| Part | Type | Required | Notes |
|------|------|----------|-------|
| `metadata` | `text/plain` JSON string | yes | Serialized JSON object. Schema below. |
| `screenshot` | `image/png` file | yes | PNG produced client-side by `html2canvas` (or any tool that emits valid PNG). |

### Metadata schema

| Field | Type | Required | Validation | Description |
|-------|------|----------|-----------|-------------|
| `protocol_version` | string | yes | MUST equal `"0.1"`. Unknown → `400 unsupported_protocol_version`. | Wire-protocol version. |
| `title` | string | yes | Non-empty after trim. Max 200 chars recommended. | User-supplied bug title. |
| `description` | string | yes | Non-empty after trim. | User-supplied free-text description. |
| `severity` | string enum | no | One of `low`, `medium`, `high`, `critical`. **Invalid values MUST be rejected with `422`** — silent coercion fails conformance. | Triage priority. |
| `status` | string enum | no | One of `open`, `investigating`, `fixed`, `closed`. Defaults to `open` on submit. **Deprecated values accepted on read.** | Workflow state. |
| `environment` | string | no | Free string; consumer-defined (e.g., `dev`, `staging`, `prod`). No enum. | Discriminator for shared collectors. |
| `client_reported_user_agent` | string | no | Plain string. | Client-supplied User-Agent. The **server-captured request-header User-Agent is the source of truth** — see [User-Agent trust boundary](#user-agent-trust-boundary). |
| `reporter` | object | no | `{ name?, email?, user_id? }` — all sub-fields optional strings, each capped at 256 characters. Adapters MUST reject longer values with `422`. Sub-fields are opaque — no format validation (consumer user IDs vary: UUIDs, emails, integers, SSO subjects). | Submitter info if the consumer knows who is logged in. |
| `page` | object | yes | `{ url, route?, viewport: {w, h}, user_agent }` — `url` and `viewport` required. `url` recommended cap 2 KiB; adapters MAY truncate longer values silently rather than reject (legitimate URLs with query strings or OAuth state can be long). `route` recommended cap 256 chars. | Where the bug happened. |
| `console_errors` | array of object | no | Each: `{ level, message, stack?, ts }`. | Recent buffered `console.error` / `console.warn` entries. |
| `network_log` | array of object | no | Each: `{ method, url, status, duration_ms, ts }`. | Recent `fetch` / `XHR` activity. |
| `app` | object | no | `{ name, version?, env?, build_sha? }`. | Consumer-supplied app context. |
| `extras` | object | no | Free-form key/value bag. | Consumer-specific context that does not fit elsewhere. |
| `client_ts` | string (ISO 8601) | yes | RFC 3339 / ISO 8601 timestamp. | When the user clicked submit, in their local clock. |

#### Severity enum

```
low | medium | high | critical
```

Adapters MUST reject any other value with `422 Unprocessable Entity`. Silent coercion (e.g., rewriting unknown values to `"medium"`) **fails conformance**. The conformance suite includes an explicit `severity: "urgent"` rejection test.

> Consumer-configurable severity values are deferred to v0.2. v0.1 locks the enum so the reference viewer can color-code severity reliably.

#### Status enum

```
open | investigating | fixed | closed
```

Adapters MUST reject unknown values with `422 Unprocessable Entity` on **write** paths (intake, status updates). Adapters MUST accept deprecated values on **read** paths (list, detail) per the [deprecated-values rule](#deprecated-values-rule-critical).

#### `environment` field

Free-form string, no enum. Consumers use it to keep dev and prod data straight when both write to the same collector. Common values: `dev`, `staging`, `prod`, but adapters MUST NOT validate against any list.

#### User-Agent trust boundary

Two distinct fields exist for User-Agent and they MUST NOT be confused:

| Field | Source | Trust |
|-------|--------|-------|
| Server-captured `user_agent` (in `page.user_agent` of stored metadata, also reflected in storage as `user_agent_server`) | Captured by the server from the HTTP request header. | **Source of truth** for any audit, security, or policy decision. |
| `client_reported_user_agent` (top-level metadata field) | Provided by the client in the JSON body. | Diagnostic only — preserved for debugging. The client may have spoofed or modified it. |

Adapters MUST capture the request-header `User-Agent` independently and MUST NOT overwrite it with the client-supplied value. The client value is preserved alongside it for diagnostic round-trip purposes.

### Size limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| Screenshot | 10 MiB | Generous cap that covers high-DPI 4K captures. Adapters MAY enforce a smaller cap and MUST return `413 Payload Too Large` when exceeded. |
| Metadata JSON | 256 KiB recommended | Console / network buffers dominate. Adapters SHOULD truncate or cap individual buffer entries (e.g., last 50 console errors, last 50 network entries) before payload assembly on the client side. |
| Total request | 11 MiB recommended | Sum of caps + multipart overhead. Adapters MAY enforce stricter limits. |

When a limit is exceeded, the adapter MUST return `413` with the documented error shape, including a `limit_bytes` field that names the actual cap.

### Response — `201 Created`

```json
{
  "id": "bug-001",
  "received_at": "2026-04-27T15:30:00Z",
  "stored_at": "file:///var/bug-fab/reports/bug-001/",
  "github_issue_url": null
}
```

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Server-assigned report ID. Default format `bug-NNN` (sequential). Adapters MAY emit `bug-{P}NNN` / `bug-{D}NNN` style if `BUG_FAB_ID_PREFIX` is set. |
| `received_at` | string (ISO 8601) | Server clock at receipt. |
| `stored_at` | string (URI) | Where the report was persisted. Typically `file://` for `FileStorage`, or an opaque DB row identifier URI for SQL backends. Treat as opaque on the client. |
| `github_issue_url` | string \| null | Set only if optional GitHub Issues sync is enabled and succeeded. Sync failures MUST log server-side and return `null` here — they MUST NOT cause the response to be non-2xx. |

### Example

```bash
curl -X POST https://example.com/api/bug-reports \
  -F 'metadata={
        "protocol_version": "0.1",
        "title": "Save button is unresponsive",
        "description": "Click does nothing on the cart page. Reproduces in Chrome 120.",
        "severity": "high",
        "environment": "prod",
        "page": {
          "url": "https://example.com/cart",
          "route": "/cart",
          "viewport": {"w": 1920, "h": 1080},
          "user_agent": "Mozilla/5.0 ..."
        },
        "console_errors": [
          {"level": "error", "message": "Cannot read property foo of undefined",
           "stack": "TypeError at handleSave (cart.js:42)", "ts": "2026-04-27T15:29:55Z"}
        ],
        "client_ts": "2026-04-27T15:29:58-07:00"
      };type=application/json' \
  -F 'screenshot=@/tmp/screenshot.png;type=image/png'
```

---

## `GET /reports` — list reports

Returns a JSON array of report summaries. Supports filtering via query params.

### Query parameters (all optional)

| Param | Type | Notes |
|-------|------|-------|
| `status` | string enum | Filter by status. Same enum as the metadata `status` field. |
| `severity` | string enum | Filter by severity. |
| `environment` | string | Filter by environment string (exact match). |
| `page` | int | Page number, 1-indexed. Default `1`. |
| `page_size` | int | Items per page. Default `20`. Max `200`. |
| `include_archived` | bool | Default `false`. Archived reports excluded by default. |

### Response — `200 OK`

```json
{
  "reports": [
    {
      "id": "bug-001",
      "title": "Save button is unresponsive",
      "severity": "high",
      "status": "open",
      "environment": "prod",
      "received_at": "2026-04-27T15:30:00Z",
      "app": {"name": "shop", "version": "1.4.2"},
      "github_issue_url": null
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "stats": {
    "open": 1,
    "investigating": 0,
    "fixed": 0,
    "closed": 0
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `reports` | array | Summary objects. Field set documented above; adapters MAY include additional summary fields but MUST include the listed fields. |
| `total` | int | Total reports matching filters (across all pages). |
| `page` | int | Current page (echoed from request). |
| `page_size` | int | Effective page size (echoed from request, possibly capped). |
| `stats` | object | Status counts across the **filtered** result set, before pagination. |

---

## `GET /reports/{id}` — fetch a report

Returns the full stored metadata for one report.

### Response — `200 OK`

```json
{
  "id": "bug-001",
  "protocol_version": "0.1",
  "received_at": "2026-04-27T15:30:00Z",
  "title": "Save button is unresponsive",
  "description": "Click does nothing on the cart page.",
  "severity": "high",
  "status": "open",
  "environment": "prod",
  "client_reported_user_agent": "Mozilla/5.0 ...",
  "reporter": {"name": null, "email": null, "user_id": null},
  "page": {
    "url": "https://example.com/cart",
    "route": "/cart",
    "viewport": {"w": 1920, "h": 1080},
    "user_agent": "Mozilla/5.0 ..."
  },
  "console_errors": [...],
  "network_log": [...],
  "app": {"name": "shop", "version": "1.4.2"},
  "extras": {},
  "client_ts": "2026-04-27T15:29:58-07:00",
  "lifecycle": [
    {"action": "created", "by": null, "at": "2026-04-27T15:30:00Z"}
  ],
  "server": {
    "stored_at": "file:///var/bug-fab/reports/bug-001/",
    "github_issue_url": null,
    "github_issue_number": null,
    "user_agent_server": "Mozilla/5.0 ...",
    "bug_fab_version": "0.1.0"
  }
}
```

The detail response is the canonical metadata round-trip plus a `server` envelope.

### Error — `404 Not Found`

If the report does not exist or has been deleted.

---

## `GET /reports/{id}/screenshot` — fetch screenshot

Returns the raw PNG screenshot. The response body is binary `image/png`, suitable for direct use as an `<img src>`.

### Response — `200 OK`

| Header | Value |
|--------|-------|
| `Content-Type` | `image/png` |
| `Content-Length` | byte size of the PNG |

### Error — `404 Not Found`

If the report or its screenshot file is missing.

---

## `PUT /reports/{id}/status` — update status

Updates a report's status and appends an entry to its lifecycle audit log.

### Request

```json
{
  "status": "fixed",
  "fix_commit": "a1b2c3d4",
  "fix_description": "Restored the missing event listener in cart.js."
}
```

| Field | Type | Required | Validation |
|-------|------|----------|-----------|
| `status` | string enum | yes | One of `open`, `investigating`, `fixed`, `closed`. Invalid → `422`. |
| `fix_commit` | string | no | Free text — commit hash, PR link, or ticket reference. |
| `fix_description` | string | no | Free text — what the fix did. |

### Behavior

1. Validate `status` against the enum (`422` on invalid).
2. Update the report's stored status.
3. Append a lifecycle entry: `{action: "status_changed", by, at, status, fix_commit?, fix_description?}` (see [Lifecycle audit log](#lifecycle-audit-log)).
4. If GitHub Issues sync is enabled and a linked issue exists: `fixed` or `closed` → close the issue; `open` or `investigating` → reopen it. Failure MUST log server-side and MUST NOT cause the status update to fail.

### Response — `200 OK`

Returns the updated full detail object (same shape as `GET /reports/{id}`).

### Error responses

| Status | Condition |
|--------|-----------|
| `404 Not Found` | Report does not exist. |
| `422 Unprocessable Entity` | `status` is missing or invalid. |

---

## `DELETE /reports/{id}` — delete a report

Permanently deletes the report metadata and screenshot. This is a hard delete — for soft-archive semantics, use `POST /bulk-archive-closed` instead.

### Response

| Status | Condition |
|--------|-----------|
| `204 No Content` | Report deleted. No body. |
| `404 Not Found` | Report does not exist. |

---

## `POST /bulk-close-fixed` — bulk close

Closes all reports currently in `fixed` status by transitioning them to `closed`. Useful as a post-release cleanup step.

### Behavior

1. Find all reports with `status == "fixed"`.
2. For each, transition status to `closed` and append a lifecycle entry.
3. If GitHub sync is enabled, propagate state changes (best-effort, failures logged).

### Response — `200 OK`

```json
{
  "closed": 7
}
```

| Field | Type | Notes |
|-------|------|-------|
| `closed` | int | Number of reports transitioned. May be `0`. |

---

## `POST /bulk-archive-closed` — bulk archive

Moves all reports currently in `closed` status to the archive area. Archived reports are excluded from `GET /reports` by default (filter with `include_archived=true` to retrieve them).

### Behavior

| Backend | Archive mechanism |
|---------|-------------------|
| `FileStorage` | Move report directory into `archive/` subfolder. |
| SQL backends | Set `archived_at` timestamp to now. |

### Response — `200 OK`

```json
{
  "archived": 12
}
```

---

## Error response shape

All non-2xx responses (except `204` and the binary `image/png` 404) use the same JSON envelope:

```json
{
  "error": "validation_error",
  "detail": "metadata.severity must be one of: low, medium, high, critical"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `error` | string | Machine-readable error code from the table below. |
| `detail` | string \| array of object | Human-readable detail or a structured list (e.g., one entry per failed field). |

### Standard error codes

| HTTP Status | `error` code | When |
|-------------|-------------|------|
| `400 Bad Request` | `validation_error` | Multipart missing required parts (`metadata`, `screenshot`); metadata JSON malformed; required fields missing. |
| `400 Bad Request` | `unsupported_protocol_version` | Submitted `protocol_version` not recognized by this adapter. |
| `413 Payload Too Large` | `payload_too_large` | Screenshot or metadata exceeds the configured cap. Body MUST include `limit_bytes`. |
| `415 Unsupported Media Type` | `unsupported_media_type` | Screenshot Content-Type is not `image/png`, or multipart Content-Type wrong. |
| `422 Unprocessable Entity` | `schema_error` | Metadata parses as JSON but fails validation (invalid enum value, wrong type, etc.). |
| `429 Too Many Requests` | `rate_limited` | Per-IP rate limit exceeded. Body SHOULD include `retry_after_seconds`. Only emitted when rate limiting is enabled. |
| `500 Internal Server Error` | `internal_error` | Unhandled server exception. |
| `503 Service Unavailable` | `storage_unavailable` | Configured storage backend cannot be reached. |

### Failure modes that MUST NOT yield non-2xx

- **GitHub sync failure** during intake or status update — log and return success with `github_issue_url: null`.
- **Lifecycle audit log write failure** during status update — depends on adapter; recommendation is to let the status change succeed and log the audit miss for operator follow-up. (This SHOULD be rare; both writes typically share the same transaction.)

---

## Idempotency and replay

`POST /bug-reports` is **not idempotent in v0.1**. Each successful submission produces a new report with a fresh server-assigned `id`. Adapters MUST NOT attempt content-hash deduplication or honor an `Idempotency-Key` header — both behaviors are reserved for a future protocol revision.

Practical implications:

- **Client retries on network failure** create duplicate reports. Frontend bundles SHOULD avoid blind auto-retry on `POST /bug-reports`; if a retry is necessary, surface the result to the user so they can confirm.
- **Bulk operations** (`POST /bulk-close-fixed`, `POST /bulk-archive-closed`) ARE idempotent at the per-report level — a report already in the target state is a no-op. The bulk response counts MUST reflect only reports actually transitioned, not the no-op set.
- **Status updates** (`PUT /reports/{id}/status`) are idempotent for the "already in target state" case at the storage level, but every successful call still appends a lifecycle entry. Adapters MAY collapse no-op updates into a non-event (returning `200` without lifecycle append) or MAY treat each call as a fresh audit event — either is conformant in v0.1; document the choice.

A v0.2 revision may introduce an optional `Idempotency-Key` header for intake. Until then, treat duplicate submissions as a UX concern, not a protocol concern.

---

## Lifecycle audit log

Every report carries a `lifecycle` array in its detail response. Each entry records one state-changing action.

### Entry shape

```json
{
  "action": "status_changed",
  "by": "alice@example.com",
  "at": "2026-04-27T16:42:11Z",
  "status": "fixed",
  "fix_commit": "a1b2c3d4",
  "fix_description": "Restored the missing event listener in cart.js."
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string enum | yes | `created` \| `status_changed` \| `deleted` \| `archived`. |
| `by` | string \| null | yes | Consumer-supplied user identifier (opaque, capped at 256 characters; v0.2 will pull from `AuthAdapter`). May be `null` if the consumer has no auth. **Adapters MAY override the client-supplied `by` value with a server-derived identity** when the adapter has its own auth context (e.g., a session cookie identifying the operator). When overriding, the server-derived value MUST be trusted over the client value. |
| `at` | string (ISO 8601) | yes | Server clock at the time of the action. |
| `status` | string | conditional | Required when `action == "status_changed"`. The new status. |
| `fix_commit` | string | optional | Same field as the `PUT /reports/{id}/status` body — preserved for forensics. |
| `fix_description` | string | optional | Same. |

### Action semantics

| Action | When |
|--------|------|
| `created` | Appended once at intake. `status` is the initial status (typically `open`). |
| `status_changed` | Appended on every successful `PUT /reports/{id}/status`. |
| `deleted` | Optional — adapters that retain a tombstone may append this; hard-delete adapters skip it. |
| `archived` | Appended for each report when `POST /bulk-archive-closed` runs. |

The lifecycle array is append-only. Adapters MUST NOT mutate or remove entries.

---

## Auth — mount-point delegation

Bug-Fab v0.1 ships **no auth abstraction**. Adapter implementers expose two routers (intake + viewer); consumers protect routes by mounting each router under a URL prefix that their existing auth middleware already covers.

| Pattern | Consequence |
|---------|-------------|
| Mount intake at `/api/`, viewer at `/admin/` | Open submit, admin-only viewer. The most common pattern. |
| Mount both at `/admin/` | Auth required for both submit and view. Suitable for internal tools. |
| Mount both unprotected | No auth. Suitable for public POCs and hobby projects. |

### What this implies for v0.1

- The viewer **cannot display submitter identity** because the protocol has no way to ask "who is logged in." Consumers wanting submitter info MUST include it in the `reporter` field of the submitted metadata.
- The viewer's `viewer_permissions` config (`can_edit_status`, `can_delete`, `can_bulk`) gates which **endpoints** are mounted on the viewer router. It is not a per-user check — that arrives with `AuthAdapter` in v0.2.

The proper `AuthAdapter` ABC is deferred to v0.2 — see [`ROADMAP.md`](./ROADMAP.md).

---

## Storage round-trip notes

Adapters MUST preserve the full submitted metadata for round-trip fidelity. Specifically:

- Unknown optional fields submitted by a future client MUST be stored verbatim and returned in `GET /reports/{id}` responses. This keeps the protocol forward-additive.
- `extras` is an opaque object — adapters MUST NOT introspect, validate, or transform its contents.
- Stored reports MUST include the `protocol_version` they were submitted under, even if the adapter has since upgraded.

---

## Notes for adapter authors

If you are writing an adapter in a non-Python stack:

1. **Read [`ADAPTERS.md`](./ADAPTERS.md)** for reference sketches in ASP.NET Core / Razor, Express, SvelteKit, and Go. Each sketch covers the routing, validation, and storage scaffolding for its stack.
2. **Run [`CONFORMANCE.md`](./CONFORMANCE.md)** against your adapter once it boots. The Python pytest plugin can target any HTTP server — your adapter does not need to be in Python to be tested.
3. **Watch for the silent-coerce trap.** The most common implementation error is silently rewriting an unknown `severity` (or other enum) to a default. The conformance suite explicitly rejects this — see [Severity enum](#severity-enum).
4. **Honor the deprecated-values rule.** A read path that rejects a deprecated value will lock consumers out of historical data forever.
5. **Capture User-Agent from the request header.** Do not trust the client-supplied value as the source of truth — see [User-Agent trust boundary](#user-agent-trust-boundary).
6. **Make GitHub sync best-effort.** A GitHub outage MUST NOT fail an otherwise-valid bug submission.

For protocol questions, file an issue on the [Bug-Fab repo](https://github.com/AZgeekster/Bug-Fab) tagged `protocol`.
