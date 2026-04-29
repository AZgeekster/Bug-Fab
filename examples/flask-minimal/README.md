# Flask minimal example

A single-file Flask app that integrates Bug-Fab without using the FastAPI
adapter. Its purpose is to **prove the wire protocol is genuinely
framework-agnostic** &mdash; the JS bundle does not care which language is on
the other side of the multipart POST, and the Pydantic schemas plus
`FileStorage` are usable from any Python web stack.

## What this demonstrates

- The Bug-Fab frontend bundle (`bug-fab.js`) submits to a Flask handler
  unmodified.
- A Flask consumer can validate submissions with `BugReportCreate.model_validate(...)`
  and persist via `FileStorage.save_report(...)` &mdash; no FastAPI runtime
  required.
- The viewer surface (list / detail / screenshot serve) implements the
  protocol's read paths with stock Flask templates.

## What this does **not** demonstrate

This example deliberately ships only the **submit + read-only viewer**
subset of the protocol. The following are documented for self-implementation
in [`docs/ADAPTERS.md`](../../docs/ADAPTERS.md) and
[`docs/PROTOCOL.md`](../../docs/PROTOCOL.md):

- Status workflow (`PUT /reports/{id}/status`)
- Hard delete (`DELETE /reports/{id}`)
- Bulk operations (`POST /bulk-close-fixed`, `POST /bulk-archive-closed`)
- Per-IP rate limiting
- GitHub Issues sync
- Auth gating

A first-party Flask adapter (`bug_fab.adapters.flask`) that ships these
out of the box is on the v0.2 roadmap; until then, the snippets in
`docs/ADAPTERS.md` (the Express sketch is conceptually closest) are the
reference for adding them yourself.

## Run it

```bash
cd examples/flask-minimal
pip install -e "../.."         # bug-fab from the local checkout
pip install flask              # not a bug-fab dependency
python main.py
```

Then open <http://localhost:8000/> and click the floating bug icon in
the bottom-right corner. Submitted reports land in `./bug_reports/` next
to `main.py`; browse them at <http://localhost:8000/admin/bug-reports>.

## File layout

```
flask-minimal/
├── README.md           this file
├── main.py             single-file Flask app (intake + viewer + demo page)
├── requirements.txt    pip-installable runtime deps
├── .gitignore          excludes the local bug_reports/ directory
└── bug_reports/        (created at first submit; gitignored)
```

## How the protocol gets honored

| Protocol concern | Where in `main.py` |
|---|---|
| Multipart parsing | `request.form["metadata"]` + `request.files["screenshot"]` |
| Schema validation | `BugReportCreate.model_validate(...)` &mdash; Pydantic owns severity-enum strictness, so silent coercion is impossible. |
| Size limit | 10 MiB cap matches the protocol; over-cap returns `413 payload_too_large` with `limit_bytes`. |
| Image type check | PNG / JPEG magic-byte sniff &mdash; `415 unsupported_media_type` on mismatch. |
| User-Agent trust boundary | `request.headers["User-Agent"]` is the source of truth; the client-supplied value is preserved separately as `client_reported_user_agent`. |
| Persistence | `FileStorage.save_report(...)` (wrapped with `asyncio.run` because Flask is sync). |
| Response shape | 201 with `{id, received_at, stored_at, github_issue_url}` per the protocol's intake response. |
| Static bundle | `/bug-fab/static/<path>` serves the vendored bundle from the `bug_fab` package. |

## Notes for production Flask consumers

- `asyncio.run` wraps each storage call. That's fine for a demo; under
  load you'd want to either run a single event loop alongside Flask or
  switch to a sync storage shim.
- Auth is **mount-point delegation** &mdash; protect `/admin/bug-reports/*`
  behind your existing Flask-Login / Flask-Security middleware. Bug-Fab
  v0.1 ships no auth abstraction (see `docs/PROTOCOL.md` &sect; Auth).
- `app.config["MAX_CONTENT_LENGTH"]` is set to 11 MiB to match the
  protocol's recommended total-request cap. Flask returns `413` itself
  for over-cap requests before any handler runs.
