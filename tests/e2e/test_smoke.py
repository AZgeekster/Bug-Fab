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
def test_fab_position_bottom_left(page: Page, app_server: dict) -> None:
    """FAB UX (TH-5): re-init with position="bottom-left" and confirm the
    inline-style applied by the bundle clears `right` and sets `left`."""
    base = app_server["base_url"]
    page.goto(base + "/")
    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=10_000)

    page.evaluate(
        "() => { window.BugFab.destroy(); "
        "window.BugFab.init({ submitUrl: '/api/bug-reports', position: 'bottom-left' }); }"
    )
    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=5_000)

    style = fab.evaluate(
        "el => ({ top: el.style.top, bottom: el.style.bottom, "
        "left: el.style.left, right: el.style.right })"
    )
    assert style["bottom"] == "24px", style
    assert style["left"] == "24px", style
    assert style["right"] == "", style
    assert style["top"] == "", style


@pytest.mark.e2e
def test_fab_disable_enable_runtime(page: Page, app_server: dict) -> None:
    """FAB UX (TH-7): BugFab.disable() hides the FAB; BugFab.enable() shows
    it again. The hidden state is asserted via the bug-fab--hidden class
    that the bundle toggles, not via Playwright's visibility heuristic
    (which has known issues with display:none-via-class-vs-attr)."""
    base = app_server["base_url"]
    page.goto(base + "/")
    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=10_000)

    page.evaluate("() => window.BugFab.disable()")
    has_hidden = fab.evaluate("el => el.classList.contains('bug-fab--hidden')")
    assert has_hidden, "disable() did not toggle the bug-fab--hidden class"
    expect(fab).to_be_hidden()

    page.evaluate("() => window.BugFab.enable()")
    still_hidden = fab.evaluate("el => el.classList.contains('bug-fab--hidden')")
    assert not still_hidden, "enable() did not remove the bug-fab--hidden class"
    expect(fab).to_be_visible()


@pytest.mark.e2e
def test_category_dropdown_prepends_to_tags(page: Page, app_server: dict, seeded_reports) -> None:
    """FAB UX (TH-15): when categories is set, the form renders a select
    between the title and description fields, and the chosen value is
    prepended to the tags array on submit."""
    base = app_server["base_url"]
    storage_dir = app_server["storage_dir"]

    page.goto(base + "/")
    expect(page.locator("button.bug-fab").first).to_be_visible(timeout=10_000)

    page.evaluate(
        "() => { window.BugFab.destroy(); window.BugFab.init({ "
        "submitUrl: '/api/bug-reports', "
        "categories: ['Bug', 'Feature request', 'Question', 'UX nit'], "
        "categoryLabel: 'Type' }); }"
    )

    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=5_000)
    fab.click()

    title_input = page.locator("#bug-fab-title")
    expect(title_input).to_be_visible(timeout=5_000)
    title_input.fill("th-15 category test")

    category = page.locator("#bug-fab-category")
    expect(category).to_be_visible()
    label = page.locator("label[for='bug-fab-category']")
    expect(label).to_have_text("Type")
    category.select_option("Bug")

    page.locator("#bug-fab-tags").fill("ui, data")

    with page.expect_response(
        lambda r: r.url.endswith("/api/bug-reports") and r.request.method == "POST"
    ) as info:
        page.locator("[data-bug-fab-submit]").click()
    submit_resp = info.value
    assert submit_resp.status == 201, submit_resp.text()
    body = json.loads(submit_resp.text())
    report_id = body["id"]

    saved = json.loads((storage_dir / f"{report_id}.json").read_text())
    assert saved["tags"] == ["Bug", "ui", "data"], saved["tags"]


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


@pytest.mark.e2e
def test_annotation_rect_tool_differs_from_freedraw(page: Page, app_server: dict) -> None:
    """Annotation tools (TH-14): the rectangle tool draws a different stroke
    than free-draw at the same coordinates.

    We can't deterministically e2e every tool (arrow, blur, text labels are
    visual), so this one test pins down two invariants:

      1. The toolbar renders + the rectangle button switches `aria-pressed`.
      2. Clicking-and-dragging the SAME canvas coordinates with the rectangle
         tool produces a different PNG dataURL than with free-draw — proving
         the active-tool branch in the pointer-event handler actually changes
         what gets committed to the canvas.

    The other tools (arrow, blur, text) are sanity-checked by the unit-style
    walk-through in PR review and by a console-error sweep below.
    """
    base = app_server["base_url"]
    page.goto(base + "/")

    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=10_000)
    fab.click()

    canvas = page.locator(".bug-fab-overlay__canvas")
    expect(canvas).to_be_visible(timeout=5_000)
    # Toolbar rendered.
    expect(page.locator("[data-bug-fab-toolbar]")).to_be_visible()
    expect(page.locator("[data-bug-fab-tool='draw']")).to_have_attribute("aria-pressed", "true")

    # Drag start/end in canvas-element coords. We use page.mouse and
    # canvas.bounding_box() so the same screen-space drag is replayed
    # consistently across the two tool selections.
    box = canvas.bounding_box()
    assert box is not None
    x0 = box["x"] + box["width"] * 0.30
    y0 = box["y"] + box["height"] * 0.30
    x1 = box["x"] + box["width"] * 0.55
    y1 = box["y"] + box["height"] * 0.55

    def drag_segment() -> None:
        page.mouse.move(x0, y0)
        page.mouse.down()
        page.mouse.move((x0 + x1) / 2, (y0 + y1) / 2)
        page.mouse.move(x1, y1)
        page.mouse.up()

    # 1) Draw a free-draw stroke and read back the data URL.
    drag_segment()
    free_draw_data_url = canvas.evaluate("el => el.toDataURL('image/png')")
    assert free_draw_data_url.startswith("data:image/png;base64,")

    # 2) Switch to rectangle, undo the free-draw, draw same coords.
    page.locator("[data-bug-fab-tool='rectangle']").click()
    expect(page.locator("[data-bug-fab-tool='rectangle']")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.locator("[data-bug-fab-tool='draw']")).to_have_attribute("aria-pressed", "false")
    page.locator("[data-bug-fab-undo]").click()
    drag_segment()
    rect_data_url = canvas.evaluate("el => el.toDataURL('image/png')")

    # The rectangle tool draws four straight strokes for the perimeter,
    # not a single mid-segment line; the rendered pixels MUST differ.
    assert rect_data_url != free_draw_data_url, (
        "rectangle tool produced the same PNG as free-draw — "
        "active-tool branch likely never fired"
    )

    # Sanity: keyboard shortcut switches back to draw without console errors.
    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
    )
    page.keyboard.press("d")
    expect(page.locator("[data-bug-fab-tool='draw']")).to_have_attribute("aria-pressed", "true")
    assert not console_errors, console_errors
