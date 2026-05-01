"""End-to-end smoke test: drive the FAB through a real browser.

What this catches that the unit / integration / conformance suites do not:

* the JS bundle's ``metadata`` shape drifts away from the server schema
  (e.g. ``module: null`` rejected by Pydantic ``str`` field);
* the viewer list template links a bare ``bug-NNN`` href that resolves
  to ``/admin/bug-NNN`` instead of ``/admin/bug-reports/bug-NNN``;
* the detail template's ``<img src="...">`` 404s because the screenshot
  route moved under ``/reports/`` but the template wasn't updated.

The test boots the harness app at ``tests/e2e/_app.py`` (a sibling FastAPI
app that honors ``BUG_FAB_E2E_STORAGE_DIR``), opens Chromium, clicks the
FAB, fills the minimum-viable form, submits, then verifies the report
shows up in the viewer list and its detail page renders correctly.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
def test_fab_submit_then_view_detail(page: Page, app_server: dict) -> None:
    base = app_server["base_url"]
    storage_dir = app_server["storage_dir"]

    # ---- 1. Submit a bug via the FAB ----
    page.goto(base + "/")

    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=10_000)
    fab.click()

    # The overlay form mounts under .bug-fab-overlay; required field is title.
    title_input = page.locator("#bug-fab-title")
    expect(title_input).to_be_visible(timeout=5_000)
    title_input.fill("e2e smoke title")
    page.locator("#bug-fab-description").fill("e2e smoke description")
    page.locator("#bug-fab-severity").select_option("low")
    page.locator("#bug-fab-type").select_option("bug")

    # Submit and wait for the network round-trip.
    with page.expect_response(
        lambda r: r.url.endswith("/api/bug-reports") and r.request.method == "POST"
    ) as info:
        page.locator("[data-bug-fab-submit]").click()
    submit_resp = info.value
    assert (
        submit_resp.status == 201
    ), f"intake POST returned {submit_resp.status}: {submit_resp.text()}"
    body = json.loads(submit_resp.text())
    report_id = body["id"]
    assert report_id.startswith("bug-"), body

    # The bundle should NOT surface "[object Object]" on success or failure;
    # the error region should remain hidden.
    error_region = page.locator("[data-bug-fab-error]")
    if error_region.count():
        # Acceptable states: hidden attr present, or text is empty.
        is_hidden = error_region.evaluate("el => el.hasAttribute('hidden')")
        text = error_region.text_content() or ""
        assert is_hidden or "[object Object]" not in text

    # ---- 2. The report landed on disk ----
    json_files = list(storage_dir.glob(f"{report_id}.json"))
    assert json_files, f"{report_id}.json not in {storage_dir}"
    png_files = list(storage_dir.glob(f"{report_id}.png"))
    assert png_files, f"{report_id}.png not in {storage_dir}"

    # ---- 3. Open viewer list, click the row, land on detail (no 404) ----
    page.goto(base + "/admin/bug-reports/")
    row = page.locator(f"[data-bug-fab-detail-href='{report_id}']").first
    expect(row).to_be_visible(timeout=5_000)

    with page.expect_navigation():
        row.click()

    assert page.url.endswith(
        f"/admin/bug-reports/{report_id}"
    ), f"detail navigation went to wrong URL: {page.url}"
    # Detail template renders the report id somewhere prominent.
    expect(page.get_by_text(report_id, exact=False)).to_be_visible()

    # ---- 4. Screenshot <img> resolves (no 404). ----
    # Probe by listening for the response triggered when the <img> loads.
    # The src is relative; we just need the network response to be 200.
    img = page.locator("img.bug-fab-screenshot").first
    expect(img).to_be_visible()
    # Pull the resolved absolute URL out of the DOM and re-fetch to be sure.
    abs_src = img.evaluate("el => el.src")
    resp = page.request.get(abs_src)
    assert resp.status == 200, f"screenshot {abs_src} returned {resp.status}"
    assert resp.headers.get("content-type", "").startswith("image/"), resp.headers
