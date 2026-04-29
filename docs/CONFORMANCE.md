# Bug-Fab Conformance — verifying your adapter

If you have implemented (or modified) a Bug-Fab adapter, this guide shows you how to verify that it correctly speaks the [wire protocol](./PROTOCOL.md).

Conformance is enforced via a **pytest plugin** shipped with the `bug-fab` package. The plugin runs a fixed set of HTTP requests against your adapter and asserts the responses match the protocol contract.

> The plugin is Python-only in v0.1 — but **it tests over HTTP**, so the adapter under test can be written in any language. v0.2 will generalize the harness into language-neutral curl-compatible fixtures so non-Python adapter authors do not need a Python install just to run conformance.

---

## TL;DR

```bash
pip install bug-fab
pytest --bug-fab-conformance --base-url=https://my-app.example.com/bug-fab
```

If every test passes, your adapter is **v0.1-compliant**. If any test fails, the failure message names the protocol clause that was violated.

---

## Installation

The conformance plugin is registered as a pytest entry-point inside `bug-fab`:

```toml
[project.entry-points.pytest11]
bug-fab-conformance = "bug_fab.conformance.plugin"
```

So a single install gets you both the package and the plugin:

```bash
pip install bug-fab
```

If you want only the plugin without the FastAPI adapter dependencies, install the slim extra:

```bash
pip install "bug-fab[conformance]"
```

This pulls only the test runner, fixtures, and pytest itself — not FastAPI / SQLAlchemy / etc.

---

## Running the suite

The plugin adds two flags to pytest:

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--bug-fab-conformance` | yes | — | Enables the conformance test suite. Without this flag, pytest skips conformance even if the plugin is installed. |
| `--base-url=URL` | yes | — | The base URL of your adapter, including the path prefix where Bug-Fab routes are mounted. Example: `https://my-app.example.com/bug-fab`. |
| `--auth-header=HEADER` | optional | none | Single HTTP header sent with every request, e.g. `--auth-header="Bearer eyJ..."`. Use this if your adapter requires auth. |
| `--skip-mutating` | optional | `false` | Skip tests that perform write operations (`POST`, `PUT`, `DELETE`, bulk ops). Useful for read-only validation against a production system. |

### Example invocations

```bash
# Local development against the FastAPI minimal example
pytest --bug-fab-conformance --base-url=http://localhost:8000

# Against a deployed adapter behind Bearer-token auth
pytest --bug-fab-conformance \
       --base-url=https://my-app.example.com/bug-fab \
       --auth-header="Bearer eyJhbGciOi..."

# Read-only smoke test against production
pytest --bug-fab-conformance \
       --base-url=https://my-app.example.com/bug-fab \
       --skip-mutating
```

### Running in CI

Drop into your existing CI pipeline. The reference Bug-Fab repo runs conformance against its own FastAPI adapter on every push:

```yaml
- name: Run conformance tests
  run: |
    python -m pip install -e ".[dev]"
    uvicorn examples.fastapi_minimal.main:app --host 0.0.0.0 --port 8000 &
    sleep 2
    pytest --bug-fab-conformance --base-url=http://localhost:8000
```

---

## What the suite covers

The suite is organized into seven test modules. Each module maps to a section of [`PROTOCOL.md`](./PROTOCOL.md).

| Module | Protocol section | What it asserts |
|--------|------------------|-----------------|
| `test_intake.py` | `POST /bug-reports` | Happy path; response shape; `id` format; `received_at` is a valid ISO 8601 timestamp; `stored_at` is a string. |
| `test_validation.py` | Severity / status enums; required fields | Invalid `severity` → `422`; invalid `status` → `422`; missing `protocol_version` → `400`; missing `metadata` part → `400`; missing `screenshot` part → `400`; non-PNG screenshot → `415`. |
| `test_deprecated_values.py` | Deprecated-values rule | `GET /reports/{id}` returns reports stored with deprecated enum values intact (the plugin pre-seeds a fixture report with `status: "resolved"` if the adapter exposes a write hook for fixture seeding, otherwise it skips this module with a documented warning). |
| `test_viewer_list.py` | `GET /reports` | Response shape (`reports`, `total`, `page`, `page_size`, `stats`); pagination; filter by `status`; filter by `severity`; filter by `environment`. |
| `test_viewer_detail.py` | `GET /reports/{id}` and `GET /reports/{id}/screenshot` | Detail response includes all required fields; `lifecycle` array present and non-empty (at least one `created` entry); `404` for missing IDs; screenshot endpoint returns `image/png` with non-zero `Content-Length`. |
| `test_status_workflow.py` | `PUT /reports/{id}/status` | Status updates succeed; lifecycle log appends; `fix_commit` and `fix_description` round-trip; invalid status → `422`; missing `status` → `422`; nonexistent report → `404`. |
| `test_bulk_ops.py` | `POST /bulk-close-fixed`, `POST /bulk-archive-closed`, `DELETE /reports/{id}` | Bulk ops return correct counts; deleted reports return `404` on subsequent fetch; archived reports excluded from `GET /reports` unless `include_archived=true`. |

---

## Required tests in detail

These specific tests are **non-negotiable**. An adapter that skips or fails any of them does not conform to v0.1.

### Intake — happy path

```
POST /bug-reports
  multipart: metadata={...valid JSON...}, screenshot=<valid PNG>
  → 201 Created
  → response.id is a non-empty string
  → response.received_at is ISO 8601
  → response.stored_at is a non-empty string
```

### Intake — missing required fields

```
POST /bug-reports
  multipart: metadata={...}, screenshot=<missing>
  → 400 Bad Request
  → response.error == "validation_error"
```

```
POST /bug-reports
  multipart: metadata={"protocol_version": "0.1"}, screenshot=<valid PNG>
  → 400 or 422
  → response.error in {"validation_error", "schema_error"}
  → response.detail mentions title and description as missing
```

### Intake — invalid severity (the silent-coerce trap)

```
POST /bug-reports
  multipart: metadata={..., "severity": "urgent"}, screenshot=<valid PNG>
  → 422 Unprocessable Entity
  → response.error == "schema_error"
  → response.detail mentions severity
```

If your adapter returns `201` here (silently coercing `"urgent"` to a default), you fail conformance. This is the most commonly violated rule.

### Intake — unknown protocol version

```
POST /bug-reports
  multipart: metadata={..., "protocol_version": "9.9"}, screenshot=<valid PNG>
  → 400 Bad Request
  → response.error == "unsupported_protocol_version"
```

### Deprecated values — accept on read

A fixture report stored with `status: "resolved"` (a value not in the current write enum) MUST still parse and return:

```
GET /reports/{fixture_id}
  → 200 OK
  → response.status == "resolved"
```

The plugin will skip this test if your adapter does not expose a fixture-seed hook; in that case, you SHOULD verify the rule manually by inserting a deprecated-value record into your storage and confirming it round-trips.

### Viewer JSON shape

```
GET /reports
  → 200 OK
  → response.reports is a list
  → response.total is an int
  → response.page == 1
  → response.page_size == 20 (default)
  → response.stats is an object with keys open / investigating / fixed / closed
```

### Status workflow round-trip

```
1. POST /bug-reports → 201, get id
2. PUT /reports/{id}/status with {"status": "investigating"}
  → 200 OK
  → response.status == "investigating"
  → response.lifecycle has 2 entries (created + status_changed)
3. PUT /reports/{id}/status with {"status": "fixed", "fix_commit": "abc123",
                                  "fix_description": "Fixed it"}
  → 200 OK
  → lifecycle[2].fix_commit == "abc123"
  → lifecycle[2].fix_description == "Fixed it"
4. PUT /reports/{id}/status with {"status": "bogus"}
  → 422 Unprocessable Entity
```

### Bulk ops — counts and idempotency

```
1. Seed 3 reports, transition all to "fixed"
2. POST /bulk-close-fixed
  → 200 OK
  → response.closed == 3
3. POST /bulk-close-fixed (again)
  → 200 OK
  → response.closed == 0
4. POST /bulk-archive-closed
  → 200 OK
  → response.archived == 3
5. GET /reports
  → 200 OK
  → response.total == 0 (archived excluded by default)
6. GET /reports?include_archived=true
  → 200 OK
  → response.total == 3
```

### Environment field — round-trip

```
POST /bug-reports with metadata.environment == "staging"
  → 201
GET /reports/{id}
  → response.environment == "staging"
GET /reports?environment=staging
  → response.reports includes the report
GET /reports?environment=prod
  → response.reports excludes the report
```

---

## Interpreting results

| Outcome | Meaning |
|---------|---------|
| **All tests pass** | Your adapter is v0.1-compliant. You can advertise this in your README. |
| **A test in `test_validation.py` fails** | Your adapter is too permissive. Most often: it is silently coercing an invalid enum value. Fix the validation, do not loosen the test. |
| **A test in `test_deprecated_values.py` fails or skips** | Your adapter is too strict on the read path, or you have not implemented the fixture-seed hook. Either way, manually insert a deprecated-value record and verify it returns. |
| **A test in `test_status_workflow.py` fails** | Either your status update endpoint does not match the protocol, or your lifecycle audit log is broken. The failure message will tell you which. |
| **A test in `test_bulk_ops.py` fails** | Bulk ops counts wrong, or archived reports leaking into default list responses. |
| **All tests skip** | You forgot the `--bug-fab-conformance` flag, or your `--base-url` is wrong. |

---

## What conformance does NOT cover (yet)

- **Frontend bundle integration.** The plugin tests the wire protocol only. You still need to manually verify that the [Bug-Fab JS frontend bundle](https://github.com/AZgeekster/Bug-Fab/tree/main/static) successfully submits to your adapter end-to-end.
- **Auth adapter conformance.** v0.1 has no auth abstraction, so there is nothing to test. v0.2 will add auth conformance once the `AuthAdapter` ABC ships.
- **Performance / load testing.** Out of scope for conformance; your adapter is allowed to be slow.
- **GitHub Issues sync.** Optional feature; if your adapter implements it, you are responsible for testing it. The conformance suite verifies only that sync failures do not break intake.
- **Specific storage backend semantics.** The plugin does not test how reports are stored — only that the HTTP shape is correct.

---

## Future — v0.2 language-neutral conformance

v0.2 will ship the conformance suite in two forms:

1. **The current pytest plugin** — for adapter authors who use Python or are happy installing it.
2. **Language-neutral HTTP fixtures** — a directory of curl scripts plus expected JSON responses, so adapter authors in any language can run conformance with no Python install.

The pytest plugin will continue to work and remain the reference. The HTTP fixtures will be generated from the same canonical fixture set the pytest plugin uses, so they cannot drift out of sync.

If you are an adapter author in a non-Python stack and v0.2 has not shipped yet, you have two options:

- Install Python just for the pytest plugin (it does not require any code changes to your adapter).
- Hand-translate the [Required tests in detail](#required-tests-in-detail) section above into your test framework of choice. The protocol is small enough that this is a few hundred lines of test code.

---

## Reporting bugs in the conformance suite

If you believe a conformance test is wrong (the protocol allows behavior X but the test rejects it), please file an issue on the [Bug-Fab repo](https://github.com/AZgeekster/Bug-Fab) tagged `conformance` and include:

- The exact pytest output, including the failing assertion.
- Which clause of [`PROTOCOL.md`](./PROTOCOL.md) you believe should permit the behavior.
- Your adapter's response body (`error` code + `detail`).

The conformance suite is maintained alongside the protocol spec; if the spec and the suite disagree, one of them is wrong and the maintainers will fix it.
