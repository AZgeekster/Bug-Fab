# bug-fab-conformance

The `bug-fab-conformance` pytest plugin runs Bug-Fab's wire-protocol
conformance suite against any HTTP server that claims to implement
**Bug-Fab v0.1**.

If the suite passes, your adapter is interoperable with the reference
frontend bundle and any other Bug-Fab consumer that speaks the protocol.

## Install

The plugin ships inside the `bug-fab` package — no separate install:

```bash
pip install bug-fab
```

## Run

```bash
pytest --bug-fab-conformance --base-url=http://localhost:8000
```

The `--base-url` is the prefix where your adapter's intake + viewer endpoints
are mounted. The plugin appends `/bug-reports`, `/reports`, etc. to that
prefix.

If your adapter requires authentication on these endpoints, pass an
optional `--auth-header`:

```bash
pytest --bug-fab-conformance \
    --base-url=https://my-app.example.com/bug-fab \
    --auth-header="Authorization: Bearer eyJ0eXAi..."
```

A non-zero exit code means at least one protocol clause is being violated.
Each failing test prints the spec clause it asserts in its message — that is
the clause to fix.

## What gets tested

Five test modules ship inside the plugin:

| Module | Endpoints covered |
|--------|-------------------|
| `test_intake.py` | `POST /bug-reports` — happy paths, validation errors, oversize, content-type, response shape |
| `test_viewer.py` | `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/screenshot` — pagination envelope, filters, 404, content-type |
| `test_status_workflow.py` | `PUT /reports/{id}/status`, `POST /bulk-close-fixed`, `POST /bulk-archive-closed` — valid transitions, invalid status, lifecycle audit log, bulk-op response shapes |
| `test_deprecated_values.py` | CC12 — deprecated `status: "resolved"` MUST be rejected on write |
| `test_environment_field.py` | CC4 — optional `environment` metadata round-trips and absence is allowed |

All tests are pure HTTP-level checks — there is no Python coupling to the
adapter under test. v0.2 will generalize the suite to a curl-compatible
fixture format so non-Python adapter authors can run conformance without
installing Python.

## Writing your own adapter-internal tests

If you also want to write your own tests against the same canonical
fixtures, import the helpers directly:

```python
from bug_fab.conformance import (
    make_test_png,
    make_test_metadata,
    make_invalid_severity_metadata,
    make_legacy_status_payload,
)
```

`make_test_png()` returns a minimal valid PNG byte sequence (no Pillow
dependency). `make_test_metadata(**overrides)` returns a JSON-string
metadata payload with sane defaults; pass any field name as a keyword to
override.

## Reporting bugs in the conformance suite itself

If you believe a conformance test misreads the protocol spec, open an
issue at <https://github.com/AZgeekster/Bug-Fab/issues> with:

- the failing test's full output,
- the section of `docs/PROTOCOL.md` you believe the test misinterprets,
- a curl reproduction of the failing request.

A bug-reporting tool that has bugs is embarrassing — bugs in the
conformance suite especially so. Reports very welcome.
