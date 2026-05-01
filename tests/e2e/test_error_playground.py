"""End-to-end tests for the public POC's error-playground demo.

The error-playground page exposes eight intentional-error buttons that
each trip one of the bug-fab bundle's capture paths (window.onerror,
console.error, unhandledrejection, fetch hook). These tests boot the
real ``examples/error-playground/main.py`` (not the smaller harness
used by ``test_smoke.py``) so a regression on either side — bundle's
capture wiring or the demo page's button glue — fails CI.

Two layers of assertion per test:

1. Clicking a red button updates the in-page log (proves the JS
   handler ran).
2. After clicking, opening the FAB and submitting a report yields a
   server-stored payload whose ``context.console_errors`` or
   ``context.network_log`` contains the captured event.

Layer 2 is what we actually care about for the POC: it's the
end-to-end proof that auto-context capture survives every part of the
chain.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="function")
def playground_server(tmp_path_factory):
    """Boot ``examples/error-playground/main:app`` on a free loopback port."""
    repo_root = Path(__file__).resolve().parents[2]
    example_dir = repo_root / "examples" / "error-playground"
    storage_dir = tmp_path_factory.mktemp("ep-storage")
    port = _free_port()

    env = os.environ.copy()
    env["BUG_FAB_STORAGE_DIR"] = str(storage_dir)
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(example_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = (proc.stdout.read() if proc.stdout else b"").decode(errors="replace")
            raise RuntimeError(f"uvicorn exited early (rc={proc.returncode}); output:\n{output}")
        try:
            r = httpx.get(base_url + "/", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("error-playground server did not come up within 20s")

    yield {"base_url": base_url, "storage_dir": storage_dir}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _submit_via_fab(page: Page, *, title: str) -> str:
    """Open the FAB, fill the minimum form, submit, return the new id.

    Waits explicitly for the FAB to be ready before clicking. The FAB
    mounts on DOMContentLoaded; under load (e.g. after a series of
    server-side 500s in the same module) the assertion is the right
    failure-mode boundary rather than a silent missed click.
    """
    fab = page.locator("button.bug-fab").first
    expect(fab).to_be_visible(timeout=10_000)
    fab.click()
    expect(page.locator("#bug-fab-title")).to_be_visible(timeout=5_000)
    page.locator("#bug-fab-title").fill(title)
    page.locator("#bug-fab-severity").select_option("low")
    with page.expect_response(
        lambda r: r.url.endswith("/api/bug-reports") and r.request.method == "POST"
    ) as info:
        page.locator("[data-bug-fab-submit]").click()
    resp = info.value
    assert resp.status == 201, f"intake POST {resp.status}: {resp.text()}"
    return json.loads(resp.text())["id"]


def _read_report(storage_dir: Path, report_id: str) -> dict[str, Any]:
    """Read the JSON the server wrote for ``report_id`` from disk."""
    path = storage_dir / f"{report_id}.json"
    assert path.is_file(), f"missing {path}"
    return json.loads(path.read_text("utf-8"))


@pytest.mark.e2e
def test_demo_endpoints_return_expected_status(playground_server) -> None:
    """The /demo/missing and /demo/explode endpoints back the failing-fetch
    buttons. Pin their status codes here so the demo never silently
    starts returning 200."""
    base = playground_server["base_url"]
    with httpx.Client(base_url=base, timeout=5) as client:
        assert client.get("/demo/missing").status_code == 404
        assert client.get("/demo/explode").status_code == 500


@pytest.mark.e2e
def test_all_demo_buttons_render(page: Page, playground_server) -> None:
    """Every button id we ship must be present on the rendered page —
    catches accidental removals or rename drift between page and JS."""
    page.goto(playground_server["base_url"] + "/")
    for btn_id in (
        "demo-throw",
        "demo-reference",
        "demo-typeerror",
        "demo-console-error",
        "demo-rejection",
        "demo-fetch-404",
        "demo-fetch-500",
        "demo-fetch-network",
    ):
        expect(page.locator(f"#{btn_id}")).to_be_visible()
    expect(page.locator("#demo-log")).to_be_visible()


@pytest.mark.e2e
def test_throw_uncaught_lands_in_console_errors(page: Page, playground_server) -> None:
    """Clicking 'Throw uncaught error' must (a) update the in-page log
    and (b) surface in the FAB submission's ``context.console_errors``."""
    base = playground_server["base_url"]
    storage_dir = playground_server["storage_dir"]

    # Playwright would otherwise fail the test on uncaught page errors;
    # we WANT the uncaught error here.
    page.on("pageerror", lambda exc: None)

    page.goto(base + "/")
    page.locator("#demo-throw").click()
    # In-page log echoes that the click ran.
    expect(page.locator("#demo-log")).to_contain_text("throwing Error")
    # Give the bundle a tick to record the captured error.
    page.wait_for_timeout(300)

    report_id = _submit_via_fab(page, title="e2e: caught uncaught throw")
    saved = _read_report(storage_dir, report_id)
    errors = saved.get("context", {}).get("console_errors") or []
    assert any(
        "Demo: synchronous throw" in (e.get("message") or "") for e in errors
    ), f"console_errors did not contain demo throw: {errors}"


@pytest.mark.e2e
def test_console_error_lands_in_console_errors(page: Page, playground_server) -> None:
    """The quiet console.error path captures without page-level error.
    Pins down the bundle's ``console.error`` hook stays installed."""
    base = playground_server["base_url"]
    storage_dir = playground_server["storage_dir"]

    page.goto(base + "/")
    page.locator("#demo-console-error").click()
    expect(page.locator("#demo-log")).to_contain_text("logging via console.error")
    page.wait_for_timeout(200)

    report_id = _submit_via_fab(page, title="e2e: console.error captured")
    saved = _read_report(storage_dir, report_id)
    errors = saved.get("context", {}).get("console_errors") or []
    assert any(
        "Demo console.error" in (e.get("message") or "") for e in errors
    ), f"console_errors did not contain console.error message: {errors}"


@pytest.mark.e2e
def test_unhandled_rejection_lands_in_console_errors(page: Page, playground_server) -> None:
    """Promise rejection hits ``unhandledrejection``, which the bundle
    folds into ``console_errors``."""
    base = playground_server["base_url"]
    storage_dir = playground_server["storage_dir"]

    page.on("pageerror", lambda exc: None)

    page.goto(base + "/")
    page.locator("#demo-rejection").click()
    expect(page.locator("#demo-log")).to_contain_text("rejecting a promise")
    page.wait_for_timeout(300)

    report_id = _submit_via_fab(page, title="e2e: promise rejection captured")
    saved = _read_report(storage_dir, report_id)
    errors = saved.get("context", {}).get("console_errors") or []
    assert any(
        "Demo: promise rejected" in (e.get("message") or "") for e in errors
    ), f"console_errors did not contain rejection: {errors}"


@pytest.mark.e2e
def test_fetch_404_lands_in_network_log(page: Page, playground_server) -> None:
    """Failing fetch records the request + response status in
    ``context.network_log``. Pins the fetch hook + the demo route."""
    base = playground_server["base_url"]
    storage_dir = playground_server["storage_dir"]

    page.goto(base + "/")
    page.locator("#demo-fetch-404").click()
    expect(page.locator("#demo-log")).to_contain_text("HTTP 404", timeout=5_000)
    page.wait_for_timeout(200)

    report_id = _submit_via_fab(page, title="e2e: 404 captured")
    saved = _read_report(storage_dir, report_id)
    network = saved.get("context", {}).get("network_log") or []
    matches = [n for n in network if "/demo/missing" in (n.get("url") or "")]
    assert matches, f"network_log did not contain /demo/missing: {network}"
    assert any(n.get("status") == 404 for n in matches), matches


@pytest.mark.e2e
def test_fetch_500_lands_in_network_log(page: Page, playground_server) -> None:
    """Server-side crash. Same shape as the 404 case but verifies the
    bundle records server-error responses, not just client-error ones."""
    base = playground_server["base_url"]
    storage_dir = playground_server["storage_dir"]

    page.goto(base + "/")
    page.locator("#demo-fetch-500").click()
    expect(page.locator("#demo-log")).to_contain_text("HTTP 500", timeout=5_000)
    page.wait_for_timeout(200)

    report_id = _submit_via_fab(page, title="e2e: 500 captured")
    saved = _read_report(storage_dir, report_id)
    network = saved.get("context", {}).get("network_log") or []
    matches = [n for n in network if "/demo/explode" in (n.get("url") or "")]
    assert matches, f"network_log did not contain /demo/explode: {network}"
    assert any(n.get("status") == 500 for n in matches), matches
