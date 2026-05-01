"""End-to-end smoke tests: drive every interactive element of the deployed
viewer through a real browser.

The point is not exhaustive coverage; it is *visible-button coverage*. If
a user can click it on the deployed POC, an e2e test should click it too.
That keeps the public bundle and the public viewer templates in lock-step
with the route shape, and surfaces drift before someone files a bug.

Each test isolates one click surface so a regression points at the right
fix without log-spelunking. They share the module-scoped ``app_server``
fixture (one uvicorn subprocess for the module) but each test gets a fresh
browser context from ``pytest-playwright``.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from playwright.sync_api import Page, expect

# Five seeded reports give the filter pills, bulk actions, and pagination
# something to act on. Severities cover both ends of the enum.
SEEDED = [
    {"title": "seeded-low", "severity": "low", "report_type": "bug"},
    {"title": "seeded-med-1", "severity": "medium", "report_type": "bug"},
    {"title": "seeded-med-2", "severity": "medium", "report_type": "bug"},
    {"title": "seeded-high", "severity": "high", "report_type": "bug"},
    {"title": "seeded-feature", "severity": "low", "report_type": "feature_request"},
]


@pytest.fixture(scope="module")
def seeded_reports(app_server):
    base = app_server["base_url"]
    ids: list[str] = []
    # Tiny 1x1 PNG.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\xdac\xf8\x0f"
        b"\x00\x01\x05\x01\x02 \x00\x01\x18\x05\x90\x14\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with httpx.Client(base_url=base, timeout=10) as client:
        for s in SEEDED:
            metadata = {
                "protocol_version": "0.1",
                "client_ts": "2026-05-01T16:00:00Z",
                "title": s["title"],
                "description": s["title"] + " body",
                "severity": s["severity"],
                "report_type": s["report_type"],
                "tags": ["seed"],
                "context": {
                    "url": base + "/",
                    "module": "",
                    "user_agent": "e2e-seed/1.0",
                    "viewport_width": 1280,
                    "viewport_height": 720,
                },
            }
            resp = client.post(
                "/api/bug-reports",
                files={"screenshot": ("s.png", png, "image/png")},
                data={"metadata": json.dumps(metadata)},
            )
            assert resp.status_code == 201, resp.text
            ids.append(resp.json()["id"])
    return ids


@pytest.mark.e2e
def test_fab_submit_then_view_detail(page: Page, app_server: dict, seeded_reports) -> None:
    """The original happy path. Submit via the FAB, then walk to the detail
    page through the viewer's row-click handler, and confirm the screenshot
    img resolves."""
    base = app_server["base_url"]
    storage_dir = app_server["storage_dir"]

    page.goto(base + "/")
    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=10_000)
    fab.click()

    title_input = page.locator("#bug-fab-title")
    expect(title_input).to_be_visible(timeout=5_000)
    title_input.fill("e2e fab submit")
    page.locator("#bug-fab-description").fill("driven by playwright")
    page.locator("#bug-fab-severity").select_option("low")
    page.locator("#bug-fab-type").select_option("bug")

    with page.expect_response(
        lambda r: r.url.endswith("/api/bug-reports") and r.request.method == "POST"
    ) as info:
        page.locator("[data-bug-fab-submit]").click()
    submit_resp = info.value
    assert submit_resp.status == 201, f"intake POST {submit_resp.status}: {submit_resp.text()}"
    body = json.loads(submit_resp.text())
    report_id = body["id"]
    assert report_id.startswith("bug-"), body

    # Bundle's error region must not be showing "[object Object]".
    error_region = page.locator("[data-bug-fab-error]")
    if error_region.count():
        text = error_region.text_content() or ""
        assert "[object Object]" not in text

    assert (storage_dir / f"{report_id}.json").exists()
    assert (storage_dir / f"{report_id}.png").exists()

    page.goto(base + "/admin/bug-reports/")
    row = page.locator(f"[data-bug-fab-detail-href='{report_id}']").first
    expect(row).to_be_visible(timeout=5_000)
    with page.expect_navigation():
        row.click()
    assert page.url.endswith(f"/admin/bug-reports/{report_id}"), page.url
    expect(page.get_by_text(report_id, exact=False)).to_be_visible()

    img = page.locator("img.bug-fab-screenshot").first
    expect(img).to_be_visible()
    abs_src = img.evaluate("el => el.src")
    resp = page.request.get(abs_src)
    assert resp.status == 200, f"screenshot {abs_src} returned {resp.status}"
    assert resp.headers.get("content-type", "").startswith("image/")


@pytest.mark.e2e
def test_copy_path_for_claude(
    page: Page, app_server: dict, browser_context_args, seeded_reports
) -> None:
    """The Copy Path for Claude button has to work both on a secure-context
    host (clipboard API) and a plain-HTTP LAN host (legacy execCommand). On
    localhost (this test's host) it takes the clipboard-API path, but the
    success/failure toast is the user-visible signal we assert on."""
    base = app_server["base_url"]
    report_id = seeded_reports[0]
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    page.goto(f"{base}/admin/bug-reports/{report_id}")

    copy_btn = page.locator("#bug-fab-copy-path")
    expect(copy_btn).to_be_visible()
    copy_btn.click()

    toast = page.locator("#bug-fab-toast")
    expect(toast).to_have_text("Copied to clipboard", timeout=3_000)

    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert clipboard == f"bug_reports/{report_id}.json", clipboard


@pytest.mark.e2e
def test_status_update(page: Page, app_server: dict, seeded_reports) -> None:
    """The status form on the detail page submits via JS to PUT
    /admin/bug-reports/reports/{id}/status, then reloads the page on
    success. Tests that the relative URL composes correctly *and* that
    the form round-trips to the storage backend."""
    base = app_server["base_url"]
    report_id = seeded_reports[1]

    page.goto(f"{base}/admin/bug-reports/{report_id}")

    status_select = page.locator("#bug-fab-status-form select[name='status']")
    expect(status_select).to_be_visible()
    status_select.select_option("investigating")

    with page.expect_response(
        lambda r: re.search(rf"/reports/{re.escape(report_id)}/status$", r.url)
        and r.request.method == "PUT"
    ) as info:
        page.locator("#bug-fab-status-form button[type='submit']").click()
    assert info.value.status == 200, info.value.text()

    # The detail JS reloads the page on success — wait for the toast then
    # for the new page to settle, then re-fetch the detail to assert the
    # status change persisted.
    page.wait_for_load_state("networkidle")
    detail = httpx.get(f"{base}/admin/bug-reports/reports/{report_id}").json()
    assert detail["status"] == "investigating", detail


@pytest.mark.e2e
def test_filter_pill_changes_status(page: Page, app_server: dict, seeded_reports) -> None:
    """Filter pills at the top of the list page push status= into the
    query string and reload."""
    base = app_server["base_url"]
    page.goto(base + "/admin/bug-reports/")
    expect(page.locator("[data-bug-fab-detail-href]").first).to_be_visible()

    pill = page.locator("[data-bug-fab-filter-status='investigating']").first
    expect(pill).to_be_visible()
    with page.expect_navigation():
        pill.click()
    assert "status=investigating" in page.url, page.url


@pytest.mark.e2e
def test_filter_form_apply(page: Page, app_server: dict, seeded_reports) -> None:
    """The Apply button in the filter form is a normal form submit; this
    pins down that it stays on the same path (no spurious redirects)."""
    base = app_server["base_url"]
    page.goto(base + "/admin/bug-reports/")

    severity_select = page.locator("#bug-fab-filter-form select[name='severity']")
    severity_select.select_option("high")
    with page.expect_navigation():
        page.locator("#bug-fab-filter-form button[type='submit']").click()
    assert "severity=high" in page.url, page.url
    assert "/admin/bug-reports" in page.url, page.url
    expect(page.locator("[data-bug-fab-detail-href]").first).to_be_visible()


@pytest.mark.e2e
def test_bulk_close_fixed(page: Page, app_server: dict, seeded_reports) -> None:
    """Bulk action button fires a POST to a sibling path of the list view.
    This caught the same trailing-slash class of bug as the row-click handler:
    relative `bulk-close-fixed` from `/admin/bug-reports` (no slash, post-307)
    resolves to `/admin/bulk-close-fixed` (404). The fix is to guarantee the
    trailing slash before resolution."""
    base = app_server["base_url"]
    page.goto(base + "/admin/bug-reports/")

    page.on("dialog", lambda d: d.accept())  # confirm() prompt

    with page.expect_response(
        lambda r: r.url.endswith("/bulk-close-fixed") and r.request.method == "POST"
    ) as info:
        page.locator("[data-bug-fab-bulk='close-fixed']").click()
    resp = info.value
    assert resp.status == 200, f"{resp.url} -> {resp.status}: {resp.text()}"
    body = json.loads(resp.text())
    assert "closed" in body, body


@pytest.mark.e2e
def test_bulk_archive_closed(page: Page, app_server: dict, seeded_reports) -> None:
    base = app_server["base_url"]
    page.goto(base + "/admin/bug-reports/")
    page.on("dialog", lambda d: d.accept())

    with page.expect_response(
        lambda r: r.url.endswith("/bulk-archive-closed") and r.request.method == "POST"
    ) as info:
        page.locator("[data-bug-fab-bulk='archive-closed']").click()
    resp = info.value
    assert resp.status == 200, f"{resp.url} -> {resp.status}: {resp.text()}"
    body = json.loads(resp.text())
    assert "archived" in body, body


@pytest.mark.e2e
def test_back_to_list_from_detail(page: Page, app_server: dict, seeded_reports) -> None:
    """The Back-to-list button on detail uses href='./' which must resolve
    back to the list view, not somewhere weird."""
    base = app_server["base_url"]
    page.goto(f"{base}/admin/bug-reports/{seeded_reports[0]}")
    with page.expect_navigation():
        page.get_by_role("link", name=re.compile("Back to list")).click()
    # FastAPI redirect_slashes will strip the slash; either form is acceptable.
    assert page.url.rstrip("/").endswith("/admin/bug-reports"), page.url
    expect(page.locator("[data-bug-fab-detail-href]").first).to_be_visible()


@pytest.mark.e2e
def test_no_object_object_in_visible_text(page: Page, app_server: dict, seeded_reports) -> None:
    """Sweep the list page and a detail page for the literal string
    '[object Object]'. Catches any future error-formatter regression
    that string-coerces a non-string."""
    base = app_server["base_url"]
    for path in ("/admin/bug-reports/", f"/admin/bug-reports/{seeded_reports[0]}"):
        page.goto(base + path)
        body = page.locator("body").text_content() or ""
        assert "[object Object]" not in body, f"found on {path}"
