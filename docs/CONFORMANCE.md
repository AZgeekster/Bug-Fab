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
pip install bug-fab pytest
```

`pytest` itself is not a runtime dependency of `bug-fab`, so install it alongside. (There is no slimmer install — the plugin ships inside the main package.)

---

## Running the suite

The plugin adds these flags to pytest (grouped under `bug-fab` in `pytest --help`):

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--bug-fab-conformance` | yes | — | Enables the conformance test suite. Without this flag, pytest skips conformance even if the plugin is installed. |
| `--base-url=URL` | yes | — | Base URL of your adapter's **intake** endpoint (the suite appends `/bug-reports`). Example: `https://my-app.example.com/api`. |
| `--viewer-base-url=URL` | optional | `--base-url` | Base URL of the **viewer** endpoints (the suite appends `/reports`, `/bulk-close-fixed`, …). Set this for split-mount adapters where intake is open and the viewer is auth-gated under a different prefix — the documented best practice. |
| `--auth-header=HEADER` | optional | none | Single HTTP header in `'Name: value'` format sent with every request, e.g. `--auth-header="Authorization: Bearer eyJ..."`. Use this if your adapter requires auth. |

The suite performs write operations (`POST`, `PUT`, `DELETE`, bulk ops) — point it at a disposable environment, never at production data you care about.

### Example invocations

```bash
# Local development against the FastAPI minimal example (split mounts)
pytest --bug-fab-conformance \
       --base-url=http://localhost:8000/api \
       --viewer-base-url=http://localhost:8000/admin/bug-reports

# Against a deployed adapter that mounts intake + viewer under one prefix,
# behind Bearer-token auth
pytest --bug-fab-conformance \
       --base-url=https://staging.example.com/bug-fab \
       --auth-header="Authorization: Bearer eyJhbGciOi..."
```

### Running in CI

Drop into your existing CI pipeline. The reference Bug-Fab repo runs conformance against its own FastAPI adapter on every push (see `.github/workflows/ci.yml` for the full job, including the boot-poll):

```yaml
- name: Run conformance tests
  run: |
    python -m pip install -e ".[dev]"
    (cd examples/fastapi-minimal && \
      uvicorn main:app --host 127.0.0.1 --port 8000 &)
    sleep 2
    pytest --bug-fab-conformance \
           --base-url=http://127.0.0.1:8000/api \
           --viewer-base-url=http://127.0.0.1:8000/admin/bug-reports
```

(The `cd` matters: the example lives in a hyphenated folder, so a dotted `examples.fastapi_minimal.main:app` import path cannot resolve.)

---

## What the suite covers

The suite is organized into five test modules (in `bug_fab/conformance/tests/`). Each module maps to a section of [`PROTOCOL.md`](./PROTOCOL.md).

| Module | Protocol section | What it asserts |
|--------|------------------|-----------------|
| `test_intake.py` | `POST /bug-reports` | Happy path; response shape; `id` format; `received_at` is a valid ISO 8601 timestamp; missing/invalid parts (missing `metadata` or `screenshot` → `400`/`422`; malformed JSON; invalid `severity` → `422`; oversize screenshot → `400`/`413`; wrong content type → `415`); unknown `protocol_version` → `400 unsupported_protocol_version`; every error body carries the `{error, detail}` envelope; the `201` body never echoes user-submitted free text. |
| `test_viewer.py` | `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/screenshot` | Pagination envelope (`items`, `total`, `page`, `page_size`, `stats`); `stats` has the four lifecycle states; filter by `status` / `severity`; detail includes the documented fields; screenshot returns `image/png`; unknown ids → `404`. |
| `test_deprecated_values.py` | Deprecated-values rule | Reports stored with deprecated enum values (e.g. `status: "resolved"`) still parse and return on read, and deprecated values are rejected on write. |
| `test_status_workflow.py` | `PUT /reports/{id}/status`, `POST /bulk-close-fixed`, `POST /bulk-archive-closed` | Status updates succeed; lifecycle log appends; `fix_commit` / `fix_description` round-trip; invalid status → `422`; nonexistent report → `404`; bulk ops return counts and archived reports leave the default listing. |
| `test_environment_field.py` | Environment round-trip | `metadata.environment` persists, returns on detail, and the `?environment=` list filter includes/excludes correctly. |

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
  → response.items is a list
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
  → response.items includes the report
GET /reports?environment=prod
  → response.items excludes the report
```

---

## Interpreting results

| Outcome | Meaning |
|---------|---------|
| **All tests pass** | Your adapter is v0.1-compliant. You can advertise this in your README. |
| **A validation test in `test_intake.py` fails** | Your adapter is too permissive. Most often: it is silently coercing an invalid enum value. Fix the validation, do not loosen the test. |
| **A test in `test_deprecated_values.py` fails** | Your adapter is too strict on the read path (deprecated values must round-trip) or too loose on the write path (they must be rejected). |
| **A test in `test_status_workflow.py` fails** | Either your status update endpoint does not match the protocol, or your lifecycle audit log is broken. The failure message will tell you which. |
| **A test in `test_viewer.py` fails** | List envelope shape, filters, `stats`, or the screenshot/detail endpoints diverge from the protocol. |
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
