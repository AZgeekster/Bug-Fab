# Browser-driven smoke tests

These tests boot a tiny FastAPI harness that mounts the Bug-Fab routers and
serves the JS bundle, then drive it through Chromium via Playwright. They
catch the class of bug that the unit / integration / conformance suites
cannot: drift between the bundle and the server, between viewer templates
and the route paths, or between the bundle's error rendering and the
server's error envelopes.

The tests are **excluded from the default `pytest` run** (they need a
browser binary that is not part of the dev extras) and live in their own
job in CI.

## Running locally

```bash
pip install -e ".[e2e]"
python -m playwright install chromium
pytest tests/e2e -v --browser chromium
```

The wire-protocol conformance plugin and `pytest-base-url` (pulled in by
`pytest-playwright`) both want to register a `--base-url` flag. The
conformance plugin defers to `pytest-base-url` when both are loaded so
the two coexist in one venv without ceremony.

## What's exercised

`test_smoke.py::test_fab_submit_then_view_detail` walks one path:

1. open `/` and confirm the FAB icon mounts;
2. click the FAB, fill title + description + severity + type, submit;
3. confirm the POST returns 201 and the response is the minimal envelope;
4. confirm the report's JSON + PNG land in the harness storage dir;
5. open `/admin/bug-reports/`, find the row by `data-bug-fab-detail-href`,
   click it, and confirm navigation lands on `/admin/bug-reports/<id>`
   (not `/admin/<id>`);
6. confirm the detail page's `<img class="bug-fab-screenshot">` resolves
   to a 200 response with an `image/*` content-type.

That's it. The point is not exhaustive coverage — it's having a single
end-to-end path that stays green so a regression in any of those wiring
points fails CI rather than getting discovered by the next person to use
the tool.

## Adding new browser tests

Use the existing `app_server` fixture (see `conftest.py`) for a fresh
storage directory + booted harness. Keep tests tight; the e2e suite runs
on every CI build and is the slowest tier — broad coverage belongs in the
unit / integration / conformance tiers, not here.
