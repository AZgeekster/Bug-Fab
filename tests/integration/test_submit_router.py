"""Integration tests for the ``POST /bug-reports`` submit router.

Drives the FastAPI ``TestClient`` against a real ``FileStorage`` backend and
asserts the full multipart contract: success path, validation errors,
oversize handling, content-type negotiation, server-captured User-Agent,
and rate limiting (when enabled via Settings).

The conformance plugin already covers the cross-adapter wire contract; these
tests focus on the bundled FastAPI adapter's *internal* behavior — for
example, that the ``request.headers["user-agent"]`` flows into the
``server_user_agent`` field on the persisted report.
"""

from __future__ import annotations

import json
from typing import Any

from bug_fab._rate_limit import RateLimiter


def _baseline_metadata() -> dict[str, Any]:
    return {
        "title": "Submit form does not clear after success",
        "report_type": "bug",
        "description": "Steps: open page; submit; observe stale form fields.",
        "expected_behavior": "Form clears on successful submission.",
        "severity": "medium",
        "tags": ["regression", "ui"],
        "context": {
            "url": "http://localhost/sample/path",
            "module": "sample",
            "user_agent": "client-supplied-ua/1.0",
            "viewport_width": 1280,
            "viewport_height": 720,
            "console_errors": [],
            "network_log": [],
            "environment": "dev",
        },
    }


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


def test_happy_path_returns_201_and_full_detail(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # Documented response shape: full BugReportDetail
    assert body["id"].startswith("bug-")
    assert body["title"] == "Submit form does not clear after success"
    assert body["status"] == "open"
    assert body["severity"] == "medium"
    assert body["lifecycle"]
    assert body["lifecycle"][0]["action"] == "created"
    # Has both server-captured + client-supplied UAs
    assert body["client_reported_user_agent"] == "client-supplied-ua/1.0"
    assert "server_user_agent" in body


def test_happy_path_persists_to_storage(app_factory, tiny_png: bytes, file_storage) -> None:
    """A successful submission writes a fetchable report through the backend."""
    client = app_factory(storage=file_storage)
    vp = getattr(client, "viewer_prefix", "")
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    bid = response.json()["id"]

    # Fetching back via the viewer route returns the same report
    detail = client.get(f"{vp}/reports/{bid}")
    assert detail.status_code == 200
    assert detail.json()["id"] == bid


# -----------------------------------------------------------------------------
# Server-captured User-Agent
# -----------------------------------------------------------------------------


def test_server_captures_user_agent_from_header(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
        headers={"User-Agent": "Mozilla/5.0 (Server-Captured)"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["server_user_agent"] == "Mozilla/5.0 (Server-Captured)"
    # Client-supplied value preserved verbatim
    assert body["client_reported_user_agent"] == "client-supplied-ua/1.0"


def test_environment_flows_through(app_factory, tiny_png: bytes) -> None:
    md = _baseline_metadata()
    md["context"]["environment"] = "staging"
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    assert response.json()["environment"] == "staging"


# -----------------------------------------------------------------------------
# Validation errors
# -----------------------------------------------------------------------------


def test_missing_screenshot_returns_400_or_422(app_factory) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
    )
    # FastAPI may return 422 for missing required form field; we accept both
    # to match the conformance suite tolerance.
    assert response.status_code in (400, 422)


def test_missing_metadata_returns_400_or_422(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code in (400, 422)


def test_invalid_json_metadata_returns_400(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": "{not valid json,,,"},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 400
    assert "metadata" in response.text.lower() or "json" in response.text.lower()


def test_invalid_severity_returns_422_no_silent_coercion(app_factory, tiny_png: bytes) -> None:
    """CC11: invalid severity MUST be rejected with 422."""
    md = _baseline_metadata()
    md["severity"] = "urgent"
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 422


def test_invalid_report_type_returns_422(app_factory, tiny_png: bytes) -> None:
    md = _baseline_metadata()
    md["report_type"] = "incident"
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 422


def test_missing_title_returns_422(app_factory, tiny_png: bytes) -> None:
    md = _baseline_metadata()
    del md["title"]
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 422


def test_empty_screenshot_returns_400(app_factory) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", b"", "image/png")},
    )
    assert response.status_code == 400


# -----------------------------------------------------------------------------
# Oversize
# -----------------------------------------------------------------------------


def test_oversize_screenshot_returns_413(app_factory, settings_factory, make_png) -> None:
    settings = settings_factory(max_upload_mb=1)
    # Build a 1.2 MiB PNG by padding the IDAT (start with valid signature)
    too_big = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (1_300_000))
    client = app_factory(settings=settings)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", too_big, "image/png")},
    )
    assert response.status_code == 413


# -----------------------------------------------------------------------------
# Content-type / magic byte
# -----------------------------------------------------------------------------


def test_non_image_bytes_return_415(app_factory) -> None:
    """Bytes without PNG/JPEG magic header must be rejected as 415."""
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", b"NOT-AN-IMAGE-FILE-BYTES", "image/png")},
    )
    assert response.status_code == 415


def test_jpeg_magic_bytes_accepted(app_factory) -> None:
    """JPEG magic bytes are accepted alongside PNG."""
    jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 200
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert response.status_code == 201


def test_json_only_post_rejected(app_factory) -> None:
    """A JSON POST with no multipart wrapper MUST be rejected."""
    client = app_factory()
    response = client.post(
        "/bug-reports",
        content=json.dumps({"title": "wrong content-type"}),
        headers={"Content-Type": "application/json"},
    )
    # 415 / 400 / 422 all acceptable per protocol
    assert response.status_code in (400, 415, 422)


# -----------------------------------------------------------------------------
# Rate limiting
# -----------------------------------------------------------------------------


def test_rate_limit_returns_429_when_exceeded(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    """When enabled, the per-IP limiter returns 429 after the cap is hit."""
    settings = settings_factory(
        rate_limit_enabled=True,
        rate_limit_max=2,
        rate_limit_window_seconds=60,
    )
    limiter = RateLimiter(max_per_window=2, window_seconds=60)
    client = app_factory(settings=settings, rate_limiter=limiter)

    payload = json.dumps(_baseline_metadata())
    files = {"screenshot": ("shot.png", tiny_png, "image/png")}
    # First two requests succeed
    assert client.post("/bug-reports", data={"metadata": payload}, files=files).status_code == 201
    assert client.post("/bug-reports", data={"metadata": payload}, files=files).status_code == 201
    # Third hits the cap
    response = client.post("/bug-reports", data={"metadata": payload}, files=files)
    assert response.status_code == 429
    assert "rate limit" in response.text.lower()


def test_rate_limit_disabled_by_default(app_factory, tiny_png: bytes) -> None:
    """Without configuring a limiter, every request is allowed."""
    client = app_factory()
    payload = json.dumps(_baseline_metadata())
    for _ in range(5):
        response = client.post(
            "/bug-reports",
            data={"metadata": payload},
            files={"screenshot": ("shot.png", tiny_png, "image/png")},
        )
        assert response.status_code == 201
