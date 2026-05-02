"""Integration tests for the viewer router (HTML + JSON management endpoints).

Drives a FastAPI ``TestClient`` against a real ``FileStorage`` backend and
asserts the documented HTTP contract for every viewer endpoint, plus the
per-permission gating that allows consumers to mount the viewer with
read-only or partially-restricted access.

The viewer router is mounted under the ``/viewer`` prefix in the test
fixture (matching how every real consumer mounts it — see
``examples/fastapi-minimal``). The submit router has no prefix so the
intake endpoint stays at ``/bug-reports``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


def _baseline_metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "protocol_version": "0.1",
        "title": "Test viewer report",
        "client_ts": "2026-04-29T12:00:00+00:00",
        "report_type": "bug",
        "description": "viewer test seed",
        "severity": "medium",
        "tags": ["viewer-test"],
        "context": {
            "url": "/x",
            "module": "modA",
            "user_agent": "client-ua/1.0",
            "viewport_width": 1024,
            "viewport_height": 768,
            "console_errors": [],
            "network_log": [],
            "environment": "dev",
        },
    }
    payload.update(overrides)
    return payload


def _seed(client, tiny_png: bytes, **overrides: Any) -> str:
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata(**overrides))},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _vp(client) -> str:
    """Return the viewer prefix the test client was built with."""
    return getattr(client, "viewer_prefix", "")


# -----------------------------------------------------------------------------
# HTML list view
# -----------------------------------------------------------------------------


def test_get_root_returns_html_list(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    _seed(client, tiny_png, title="Visible in HTML")
    response = client.get(vp or "/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Visible in HTML" in response.text


def test_get_html_detail_view(app_factory, tiny_png: bytes) -> None:
    """GET /{report_id} (without /screenshot) renders the HTML detail page."""
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png, title="HTML detail check")
    response = client.get(f"{vp}/{bid}")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_get_html_detail_unknown_id_returns_404(app_factory) -> None:
    client = app_factory()
    vp = _vp(client)
    response = client.get(f"{vp}/bug-999")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "context_url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
    ],
)
def test_detail_view_blocks_unsafe_url_schemes(
    app_factory, tiny_png: bytes, context_url: str
) -> None:
    """`context.url` schemes outside http(s)/relative are not rendered as href."""
    client = app_factory()
    vp = _vp(client)
    md = _baseline_metadata()
    md["context"]["url"] = context_url
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201, response.text
    bid = response.json()["id"]
    detail = client.get(f"{vp}/{bid}")
    assert detail.status_code == 200
    body = detail.text
    # The unsafe URL must NOT appear as an href value. The text MAY appear
    # inside an HTML-escaped <span> per the safe-URL allowlist UX, but never
    # inside a clickable href="...".
    assert f'href="{context_url}"' not in body
    # The Reproduce button is suppressed when the URL is unsafe.
    assert ">Reproduce<" not in body and ">\n          Reproduce" not in body


def test_detail_view_renders_safe_url_as_href(app_factory, tiny_png: bytes) -> None:
    """Standard http/https URLs DO render as href on the Reproduce + URL row."""
    client = app_factory()
    vp = _vp(client)
    md = _baseline_metadata()
    md["context"]["url"] = "https://example.com/page"
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    bid = response.json()["id"]
    detail = client.get(f"{vp}/{bid}")
    assert detail.status_code == 200
    assert 'href="https://example.com/page"' in detail.text


def test_list_filter_by_module_query_param(app_factory, tiny_png: bytes) -> None:
    """The ``module`` query param filters list results."""
    client = app_factory()
    vp = _vp(client)
    md_a = {
        "protocol_version": "0.1",
        "title": "a",
        "client_ts": "2026-04-29T12:00:00+00:00",
        "context": {"module": "alpha"},
    }
    md_b = {
        "protocol_version": "0.1",
        "title": "b",
        "client_ts": "2026-04-29T12:00:00+00:00",
        "context": {"module": "beta"},
    }
    client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md_a)},
        files={"screenshot": ("s.png", tiny_png, "image/png")},
    )
    client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md_b)},
        files={"screenshot": ("s.png", tiny_png, "image/png")},
    )
    response = client.get(f"{vp}/reports", params={"module": "alpha"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "a"


def test_html_list_filter_query_strips_whitespace(app_factory, tiny_png: bytes) -> None:
    """Whitespace-only filter values are stripped."""
    client = app_factory()
    vp = _vp(client)
    _seed(client, tiny_png)
    # Whitespace is stripped — the request still lists every report
    response = client.get(f"{vp}/reports", params={"status": "   "})
    assert response.status_code == 200
    assert response.json()["total"] == 1


# -----------------------------------------------------------------------------
# JSON list endpoint
# -----------------------------------------------------------------------------


def test_get_reports_returns_pagination_envelope(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    _seed(client, tiny_png, title="A")
    _seed(client, tiny_png, title="B")
    response = client.get(f"{vp}/reports")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {"items", "total", "page", "page_size"}
    assert body["total"] == 2
    assert body["page"] == 1
    assert isinstance(body["items"], list)


def test_get_reports_includes_stats_block(app_factory, tiny_png: bytes) -> None:
    """`GET /reports` MUST return the documented `stats` block.

    Per PROTOCOL.md § "GET /reports", the list response includes a
    `stats` object keyed by the four lifecycle states (open,
    investigating, fixed, closed). Always emitted, even when zero, so
    stat-card UIs have a stable shape.
    """
    client = app_factory()
    vp = _vp(client)
    fixed_id = _seed(client, tiny_png, title="A")
    _seed(client, tiny_png, title="B")
    client.put(f"{vp}/reports/{fixed_id}/status", json={"status": "fixed"})

    response = client.get(f"{vp}/reports")
    assert response.status_code == 200
    body = response.json()
    assert "stats" in body, f"list response missing `stats` block; got keys {sorted(body)}"
    stats = body["stats"]
    assert set(stats.keys()) == {"open", "investigating", "fixed", "closed"}
    for key, value in stats.items():
        assert isinstance(value, int), f"stats[{key!r}] must be an int; got {type(value).__name__}"
    assert stats["open"] == 1
    assert stats["fixed"] == 1
    assert stats["investigating"] == 0
    assert stats["closed"] == 0


def test_reports_filter_by_status(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    fixed_id = _seed(client, tiny_png, title="A")
    _seed(client, tiny_png, title="B")
    client.put(f"{vp}/reports/{fixed_id}/status", json={"status": "fixed"})

    response = client.get(f"{vp}/reports", params={"status": "fixed"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == fixed_id


def test_reports_filter_by_severity(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    _seed(client, tiny_png, severity="critical")
    _seed(client, tiny_png, severity="low")
    response = client.get(f"{vp}/reports", params={"severity": "critical"})
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_reports_pagination(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    for i in range(3):
        _seed(client, tiny_png, title=f"Report {i}")
    response = client.get(f"{vp}/reports", params={"page": 1, "page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["page_size"] == 2


# -----------------------------------------------------------------------------
# Detail endpoint
# -----------------------------------------------------------------------------


def test_get_report_detail_returns_json(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png, title="Detail check")
    response = client.get(f"{vp}/reports/{bid}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == bid
    assert body["title"] == "Detail check"
    assert "lifecycle" in body


def test_get_report_detail_unknown_id_returns_404(app_factory) -> None:
    client = app_factory()
    vp = _vp(client)
    response = client.get(f"{vp}/reports/bug-999")
    assert response.status_code == 404


def test_get_report_detail_invalid_id_returns_404(app_factory) -> None:
    client = app_factory()
    vp = _vp(client)
    response = client.get(f"{vp}/reports/bug-traversal-attempt")
    assert response.status_code == 404


# -----------------------------------------------------------------------------
# Screenshot endpoint
# -----------------------------------------------------------------------------


def test_get_screenshot_returns_image_png(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png, title="Screenshot test")
    # Path matches PROTOCOL.md: GET /reports/{id}/screenshot under the viewer prefix.
    response = client.get(f"{vp}/reports/{bid}/screenshot")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("image/png")
    assert response.content.startswith(b"\x89PNG")


def test_get_screenshot_unknown_id_returns_404(app_factory) -> None:
    client = app_factory()
    vp = _vp(client)
    response = client.get(f"{vp}/reports/bug-999/screenshot")
    assert response.status_code == 404


# -----------------------------------------------------------------------------
# Status update
# -----------------------------------------------------------------------------


def test_put_status_valid_succeeds_and_appends_lifecycle(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    response = client.put(
        f"{vp}/reports/{bid}/status",
        json={"status": "investigating", "fix_commit": "", "fix_description": ""},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "investigating"
    assert any(e["action"] == "status_changed" for e in body["lifecycle"])


def test_put_status_invalid_returns_422(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    response = client.put(f"{vp}/reports/{bid}/status", json={"status": "unknown"})
    assert response.status_code == 422


def test_put_status_deprecated_returns_422(app_factory, tiny_png: bytes) -> None:
    """CC12 (write half): deprecated ``resolved`` value MUST be rejected."""
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    response = client.put(f"{vp}/reports/{bid}/status", json={"status": "resolved"})
    assert response.status_code == 422


def test_put_status_unknown_id_returns_404(app_factory) -> None:
    client = app_factory()
    vp = _vp(client)
    response = client.put(f"{vp}/reports/bug-999/status", json={"status": "fixed"})
    assert response.status_code == 404


# -----------------------------------------------------------------------------
# Delete endpoint
# -----------------------------------------------------------------------------


def test_delete_report_returns_204(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    response = client.delete(f"{vp}/reports/{bid}")
    assert response.status_code == 204
    # GET now returns 404
    assert client.get(f"{vp}/reports/{bid}").status_code == 404


def test_delete_unknown_id_returns_404(app_factory) -> None:
    client = app_factory()
    vp = _vp(client)
    response = client.delete(f"{vp}/reports/bug-999")
    assert response.status_code == 404


# -----------------------------------------------------------------------------
# Bulk endpoints
# -----------------------------------------------------------------------------


def test_bulk_close_fixed_returns_count(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    client.put(f"{vp}/reports/{bid}/status", json={"status": "fixed"})

    response = client.post(f"{vp}/bulk-close-fixed")
    assert response.status_code == 200
    body = response.json()
    assert body == {"closed": 1}


def test_bulk_archive_closed_returns_count(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    client.put(f"{vp}/reports/{bid}/status", json={"status": "closed"})

    response = client.post(f"{vp}/bulk-archive-closed")
    assert response.status_code == 200
    body = response.json()
    assert body == {"archived": 1}


# -----------------------------------------------------------------------------
# Permission gating
# -----------------------------------------------------------------------------


def test_delete_forbidden_when_can_delete_disabled(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    settings = settings_factory(
        viewer_permissions={"can_edit_status": True, "can_delete": False, "can_bulk": True}
    )
    client = app_factory(settings=settings)
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    response = client.delete(f"{vp}/reports/{bid}")
    assert response.status_code == 403


def test_status_update_forbidden_when_disabled(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    settings = settings_factory(
        viewer_permissions={"can_edit_status": False, "can_delete": True, "can_bulk": True}
    )
    client = app_factory(settings=settings)
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    response = client.put(f"{vp}/reports/{bid}/status", json={"status": "fixed"})
    assert response.status_code == 403


@pytest.mark.parametrize("path_suffix", ["/bulk-close-fixed", "/bulk-archive-closed"])
def test_bulk_endpoints_forbidden_when_disabled(
    app_factory, settings_factory, tiny_png: bytes, path_suffix: str
) -> None:
    settings = settings_factory(
        viewer_permissions={"can_edit_status": True, "can_delete": True, "can_bulk": False}
    )
    client = app_factory(settings=settings)
    vp = _vp(client)
    _seed(client, tiny_png)
    response = client.post(f"{vp}{path_suffix}")
    assert response.status_code == 403


def test_ensure_status_payload_helper_validates() -> None:
    """The standalone validation helper round-trips and rejects bad data."""
    from bug_fab.routers.viewer import _ensure_status_payload

    body = _ensure_status_payload({"status": "fixed", "fix_commit": "x"})
    assert body.status.value == "fixed"
    with pytest.raises(ValueError):
        _ensure_status_payload({"status": "resolved"})


def test_read_endpoints_remain_open_when_destructive_disabled(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    """Read endpoints (list, detail, screenshot) ignore destructive flags."""
    settings = settings_factory(
        viewer_permissions={
            "can_edit_status": False,
            "can_delete": False,
            "can_bulk": False,
        }
    )
    client = app_factory(settings=settings)
    vp = _vp(client)
    bid = _seed(client, tiny_png)
    assert client.get(vp).status_code == 200
    assert client.get(f"{vp}/reports").status_code == 200
    assert client.get(f"{vp}/reports/{bid}").status_code == 200
    assert client.get(f"{vp}/reports/{bid}/screenshot").status_code == 200
