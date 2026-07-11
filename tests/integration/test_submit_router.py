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

import pytest

import bug_fab.routers.submit as submit_module
from bug_fab._rate_limit import RateLimiter


def _baseline_metadata() -> dict[str, Any]:
    return {
        "protocol_version": "0.1",
        "title": "Submit form does not clear after success",
        "client_ts": "2026-04-29T12:00:00+00:00",
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
    # Documented response shape per PROTOCOL.md § Response: minimal envelope
    # ONLY — `id`, `received_at`, `stored_at`, `github_issue_url`. Privacy
    # invariant: the response body MUST NOT echo user-submitted free text.
    assert set(body.keys()) == {"id", "received_at", "stored_at", "github_issue_url"}
    assert body["id"].startswith("bug-")
    assert body["github_issue_url"] is None
    # Specifically: title / description / severity MUST NOT leak into the envelope.
    assert "title" not in body
    assert "description" not in body
    assert "severity" not in body


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
    """Server-captured UA + client-reported UA both round-trip via GET detail.

    The intake response is the minimal envelope (no UA fields), so we follow
    up with GET /reports/{id} to confirm both UA values landed.
    """
    client = app_factory()
    vp = getattr(client, "viewer_prefix", "")
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
        headers={"User-Agent": "Mozilla/5.0 (Server-Captured)"},
    )
    assert response.status_code == 201
    bid = response.json()["id"]

    detail = client.get(f"{vp}/reports/{bid}").json()
    assert detail["server_user_agent"] == "Mozilla/5.0 (Server-Captured)"
    # Client-supplied value preserved verbatim
    assert detail["client_reported_user_agent"] == "client-supplied-ua/1.0"


def test_environment_flows_through(app_factory, tiny_png: bytes) -> None:
    """Environment field round-trips via GET detail (intake response is minimal)."""
    md = _baseline_metadata()
    md["context"]["environment"] = "staging"
    client = app_factory()
    vp = getattr(client, "viewer_prefix", "")
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    bid = response.json()["id"]
    detail = client.get(f"{vp}/reports/{bid}").json()
    assert detail["environment"] == "staging"


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


def test_oversize_metadata_returns_413(app_factory, settings_factory, tiny_png: bytes) -> None:
    """A tiny screenshot with a giant metadata string must be rejected before json.loads.

    Only the screenshot was bounded before, so a valid PNG plus a
    several-hundred-MB metadata string was parsed into memory and persisted.
    """
    settings = settings_factory(max_metadata_kb=8)
    md = _baseline_metadata()
    md["description"] = "A" * (16 * 1024)  # 16 KiB — over the 8 KiB cap
    client = app_factory(settings=settings)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert body["limit_bytes"] == 8 * 1024


def test_metadata_at_the_cap_is_accepted(app_factory, settings_factory, tiny_png: bytes) -> None:
    """The bound is a ceiling, not an off-by-one reject of legitimate reports."""
    settings = settings_factory(max_metadata_kb=256)
    client = app_factory(settings=settings)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201


# -----------------------------------------------------------------------------
# Content-type / magic byte
# -----------------------------------------------------------------------------


def test_non_image_bytes_return_415(app_factory) -> None:
    """Bytes without the PNG magic header must be rejected as 415."""
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", b"NOT-AN-IMAGE-FILE-BYTES", "image/png")},
    )
    assert response.status_code == 415


def test_jpeg_magic_bytes_rejected_as_415(app_factory) -> None:
    """JPEG bytes MUST be rejected; v0.1 locks the wire format to PNG.

    PROTOCOL.md § Request requires ``image/png`` for the screenshot part.
    The bundled ``html2canvas`` client only emits PNG, and the protocol-
    validation layer (``bug_fab/intake.py``) already enforces PNG-only;
    the router previously drifted by accepting JPEG too. This test pins
    the tightened contract so future regressions surface immediately.
    """
    jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 200
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert response.status_code == 415
    assert "png" in response.text.lower()


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


def test_spoofed_forwarded_for_does_not_evade_rate_limit(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    """S3f: with no trusted proxies, a rotating ``X-Forwarded-For`` cannot mint
    a fresh bucket per request — the untrusted peer address keys the limiter."""
    settings = settings_factory(
        rate_limit_enabled=True,
        rate_limit_max=1,
        rate_limit_window_seconds=60,
    )
    limiter = RateLimiter(max_per_window=1, window_seconds=60)
    client = app_factory(settings=settings, rate_limiter=limiter)

    payload = json.dumps(_baseline_metadata())
    files = {"screenshot": ("shot.png", tiny_png, "image/png")}
    first = client.post(
        "/bug-reports",
        data={"metadata": payload},
        files=files,
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert first.status_code == 201
    # A different spoofed header from the same (untrusted) peer is still capped.
    second = client.post(
        "/bug-reports",
        data={"metadata": payload},
        files=files,
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert second.status_code == 429


def test_oversized_content_length_rejected_before_parse(
    app_factory, settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S5: a body whose Content-Length exceeds the total cap is rejected 413
    before the multipart body is parsed.

    Proven by sending a body that is NOT valid multipart: only the
    pre-parse guard can produce a 413 here — the per-field size checks
    never run because the body would fail parsing first (a reverted guard
    yields a 4xx parse/validation error, not 413).
    """
    settings = settings_factory(max_upload_mb=1)  # total cap ~1.27 MiB
    # The custom route reads settings via the module-level get_settings(),
    # which the configure() path populates in production; dependency
    # overrides (used by app_factory) don't reach it, so set it directly.
    monkeypatch.setattr(submit_module, "_SETTINGS", settings)
    client = app_factory(settings=settings)

    oversized = b"x" * (2 * 1024 * 1024)
    response = client.post(
        "/bug-reports",
        content=oversized,
        headers={"content-type": "multipart/form-data; boundary=zzz"},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    expected = 1 * 1024 * 1024 + settings.max_metadata_kb * 1024 + 16 * 1024
    assert body["limit_bytes"] == expected


# -----------------------------------------------------------------------------
# Protocol error envelope (PROTOCOL.md § Error response shape)
#
# Every non-2xx body carries {"error": <code>, "detail": ...}. The reference
# adapter used to raise bare HTTPException, emitting {"detail": ...} with no
# machine-readable code — non-conformant while Flask and Django were correct.
# Nothing asserted the body shape, so nothing caught it.
# -----------------------------------------------------------------------------


def test_unknown_protocol_version_returns_400_unsupported_protocol_version(
    app_factory, tiny_png: bytes
) -> None:
    """An unknown version is 400 `unsupported_protocol_version`, never a 422 schema error.

    `BugReportCreate.protocol_version` is `Literal["0.1"]`, so the version
    must be checked before Pydantic validation or it surfaces as a 422.
    """
    md = _baseline_metadata()
    md["protocol_version"] = "9.9"
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_protocol_version"


def test_bad_json_metadata_uses_validation_error_envelope(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": "{not valid json,,,"},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


def test_schema_failure_uses_schema_error_envelope_with_field_list(
    app_factory, tiny_png: bytes
) -> None:
    """`detail` carries Pydantic's per-field error list, not a flat string."""
    md = _baseline_metadata()
    md["severity"] = "urgent"
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(md)},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "schema_error"
    assert isinstance(body["detail"], list)


def test_oversize_screenshot_envelope_includes_limit_bytes(app_factory, settings_factory) -> None:
    """PROTOCOL.md § Standard error codes: a 413 body MUST include `limit_bytes`."""
    settings = settings_factory(max_upload_mb=1)
    too_big = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 1_300_000)
    client = app_factory(settings=settings)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", too_big, "image/png")},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert body["limit_bytes"] == 1 * 1024 * 1024


def test_non_png_envelope_uses_unsupported_media_type(app_factory) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", b"NOT-AN-IMAGE-FILE-BYTES", "image/png")},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"


def test_empty_screenshot_envelope_uses_validation_error(app_factory) -> None:
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", b"", "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


def test_rate_limited_envelope_includes_retry_after_seconds(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    """PROTOCOL.md § Standard error codes: a 429 body SHOULD include `retry_after_seconds`."""
    settings = settings_factory(
        rate_limit_enabled=True,
        rate_limit_max=1,
        rate_limit_window_seconds=60,
    )
    limiter = RateLimiter(max_per_window=1, window_seconds=60)
    client = app_factory(settings=settings, rate_limiter=limiter)
    payload = json.dumps(_baseline_metadata())
    files = {"screenshot": ("shot.png", tiny_png, "image/png")}
    assert client.post("/bug-reports", data={"metadata": payload}, files=files).status_code == 201
    response = client.post("/bug-reports", data={"metadata": payload}, files=files)
    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "rate_limited"
    assert body["retry_after_seconds"] == 60


def test_successful_submit_still_returns_201_intake_envelope(app_factory, tiny_png: bytes) -> None:
    """Returning `JSONResponse` on error paths must not disturb the 201 success path.

    FastAPI bypasses `response_model` when a handler returns a `Response`;
    this pins that the happy path still goes through the model.
    """
    client = app_factory()
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert "error" not in body
    assert set(body) == {"id", "received_at", "stored_at", "github_issue_url"}


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
