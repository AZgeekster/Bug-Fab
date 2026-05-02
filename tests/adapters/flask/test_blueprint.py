"""Integration tests for ``bug_fab.adapters.flask.make_blueprint``.

Drives a real :class:`flask.Flask` test client against the blueprint
mounted at ``/bug-fab``, with :class:`FileStorage` rooted at a per-test
``tmp_path``. Asserts the protocol contract on every endpoint and
checks the cross-cutting concerns the FastAPI tests already cover for
that adapter — server-captured User-Agent, viewer permissions, error
envelope shape, and the static-bundle serve.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

flask = pytest.importorskip("flask")  # skip module entirely on missing extra

from bug_fab.adapters.flask import make_blueprint  # noqa: E402  - import after skip
from bug_fab.config import Settings  # noqa: E402
from bug_fab.storage.files import FileStorage  # noqa: E402

# Reuse the unit-test fixtures (``tiny_png``, ``valid_metadata_dict``)
# without importing them — the project's pytest conftest auto-discovers
# them, but our test directory is a sibling of ``tests/integration/``
# rather than nested. Pytest will walk up to the root conftest.


def _make_app(
    *,
    tmp_path: Path,
    settings: Settings | None = None,
    storage: Any = None,
) -> tuple[flask.Flask, flask.testing.FlaskClient]:
    """Build a fresh Flask app with the Bug-Fab blueprint mounted."""
    if settings is None:
        settings = Settings(storage_dir=tmp_path / "bug_reports")
    if storage is None:
        storage = FileStorage(storage_dir=settings.storage_dir)
    app = flask.Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024
    app.register_blueprint(make_blueprint(settings, storage=storage), url_prefix="/bug-fab")
    return app, app.test_client()


# -----------------------------------------------------------------------------
# Intake (POST /bug-reports)
# -----------------------------------------------------------------------------


def test_submit_returns_201_and_minimal_envelope(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Happy-path submit returns the four-field protocol envelope."""
    _, client = _make_app(tmp_path=tmp_path)
    resp = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(tiny_png), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.data
    body = resp.get_json()
    assert set(body.keys()) == {"id", "received_at", "stored_at", "github_issue_url"}
    assert body["id"].startswith("bug-")
    assert body["github_issue_url"] is None
    # Privacy invariant — user-submitted free text MUST NOT leak into the
    # intake envelope. Mirrors the FastAPI submit-router test.
    assert "title" not in body
    assert "description" not in body


def test_submit_persists_and_round_trips_via_get(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """A successful submission becomes fetchable via GET /reports/<id>."""
    _, client = _make_app(tmp_path=tmp_path)
    submit = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(tiny_png), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert submit.status_code == 201
    rid = submit.get_json()["id"]
    detail = client.get(f"/bug-fab/reports/{rid}")
    assert detail.status_code == 200
    body = detail.get_json()
    assert body["id"] == rid
    assert body["title"] == valid_metadata_dict["title"]


def test_submit_captures_server_user_agent(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Server-captured UA wins over client-supplied UA in storage."""
    _, client = _make_app(tmp_path=tmp_path)
    valid_metadata_dict["context"]["user_agent"] = "client-spoofed/1.0"
    submit = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(tiny_png), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
        headers={"User-Agent": "real-browser/2.0"},
    )
    rid = submit.get_json()["id"]
    detail = client.get(f"/bug-fab/reports/{rid}").get_json()
    assert detail["server_user_agent"] == "real-browser/2.0"
    assert detail["client_reported_user_agent"] == "client-spoofed/1.0"


def test_submit_rejects_missing_metadata(tmp_path: Path, tiny_png: bytes) -> None:
    """Missing ``metadata`` form part returns the protocol's 400 envelope."""
    _, client = _make_app(tmp_path=tmp_path)
    resp = client.post(
        "/bug-fab/bug-reports",
        data={"screenshot": (tiny_png, "shot.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "validation_error"


def test_submit_rejects_invalid_severity_with_422(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Severity outside the locked enum yields ``schema_error`` per protocol."""
    _, client = _make_app(tmp_path=tmp_path)
    valid_metadata_dict["severity"] = "urgent"  # not in {low, medium, high, critical}
    resp = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(tiny_png), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "schema_error"


def test_submit_rejects_non_png_with_415(
    tmp_path: Path,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Non-PNG bytes (or wrong content-type) yields 415 unsupported_media_type."""
    _, client = _make_app(tmp_path=tmp_path)
    resp = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(b"GIF89a-fake-bytes"), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 415
    body = resp.get_json()
    assert body["error"] == "unsupported_media_type"


def test_submit_rejects_oversized_screenshot_with_413(
    tmp_path: Path,
    make_png: Callable[..., bytes],
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Screenshot above the cap yields 413 with ``limit_bytes`` populated."""
    settings = Settings(storage_dir=tmp_path / "bug_reports", max_upload_mb=1)
    _, client = _make_app(tmp_path=tmp_path, settings=settings)
    big_png = make_png(width=600, height=600)
    # ensure we exceed the 1 MiB cap; if not, pad with a synthetic block
    # large enough to trip the cap without hitting Flask's MAX_CONTENT_LENGTH.
    if len(big_png) <= 1 * 1024 * 1024:
        big_png = big_png + b"\x00" * (1 * 1024 * 1024 + 1 - len(big_png))
    resp = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(big_png), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413
    body = resp.get_json()
    assert body["error"] == "payload_too_large"
    assert "limit_bytes" in body


# -----------------------------------------------------------------------------
# Viewer JSON paths
# -----------------------------------------------------------------------------


def _seed_one(client: flask.testing.FlaskClient, metadata: dict, png: bytes) -> str:
    """Helper that submits a single report and returns its assigned id."""
    resp = client.post(
        "/bug-fab/bug-reports",
        data={"metadata": json.dumps(metadata), "screenshot": (BytesIO(png), "s.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.data
    return resp.get_json()["id"]


def test_list_returns_protocol_envelope(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """``GET /reports`` returns ``items / total / page / page_size / stats``."""
    _, client = _make_app(tmp_path=tmp_path)
    _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.get("/bug-fab/reports")
    assert resp.status_code == 200
    body = resp.get_json()
    assert {"items", "total", "page", "page_size", "stats"} <= set(body.keys())
    assert body["total"] == 1
    assert body["stats"]["open"] == 1


def test_get_screenshot_returns_png_bytes(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """``GET /reports/<id>/screenshot`` serves the stored PNG byte-for-byte."""
    _, client = _make_app(tmp_path=tmp_path)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.get(f"/bug-fab/reports/{rid}/screenshot")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data == tiny_png


def test_get_unknown_report_returns_404(tmp_path: Path) -> None:
    """A non-existent (but well-formed) id returns 404."""
    _, client = _make_app(tmp_path=tmp_path)
    resp = client.get("/bug-fab/reports/bug-999")
    assert resp.status_code == 404


def test_malformed_id_returns_404(tmp_path: Path) -> None:
    """An id that fails the path-traversal guard returns 404, not 500."""
    _, client = _make_app(tmp_path=tmp_path)
    resp = client.get("/bug-fab/reports/..%2Fetc%2Fpasswd")
    assert resp.status_code == 404


# -----------------------------------------------------------------------------
# Status workflow
# -----------------------------------------------------------------------------


def test_status_update_appends_lifecycle_entry(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """``PUT /reports/<id>/status`` mutates the report and appends an entry."""
    _, client = _make_app(tmp_path=tmp_path)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.put(
        f"/bug-fab/reports/{rid}/status",
        json={"status": "fixed", "fix_commit": "abc123"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "fixed"
    actions = [event["action"] for event in body["lifecycle"]]
    assert "status_changed" in actions


def test_status_update_rejects_invalid_status_with_422(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Invalid status enum value yields ``schema_error`` per protocol."""
    _, client = _make_app(tmp_path=tmp_path)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.put(f"/bug-fab/reports/{rid}/status", json={"status": "wishful_thinking"})
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "schema_error"


def test_status_update_blocked_when_permission_disabled(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """``can_edit_status=False`` returns 403 even on otherwise-valid bodies."""
    settings = Settings(
        storage_dir=tmp_path / "bug_reports",
        viewer_permissions={"can_edit_status": False, "can_delete": True, "can_bulk": True},
    )
    _, client = _make_app(tmp_path=tmp_path, settings=settings)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.put(f"/bug-fab/reports/{rid}/status", json={"status": "fixed"})
    assert resp.status_code == 403


# -----------------------------------------------------------------------------
# Delete + bulk
# -----------------------------------------------------------------------------


def test_delete_returns_204_then_404(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Hard delete returns 204, and a follow-up GET returns 404."""
    _, client = _make_app(tmp_path=tmp_path)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.delete(f"/bug-fab/reports/{rid}")
    assert resp.status_code == 204
    follow = client.get(f"/bug-fab/reports/{rid}")
    assert follow.status_code == 404


def test_bulk_close_fixed_transitions_only_fixed(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """``POST /bulk-close-fixed`` closes ``fixed`` reports and reports the count."""
    _, client = _make_app(tmp_path=tmp_path)
    rid_a = _seed_one(client, valid_metadata_dict, tiny_png)
    rid_b = _seed_one(client, valid_metadata_dict, tiny_png)
    # Move one to fixed.
    client.put(f"/bug-fab/reports/{rid_a}/status", json={"status": "fixed"})
    resp = client.post("/bug-fab/bulk-close-fixed")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["closed"] == 1
    # Verify rid_a is now closed and rid_b is still open.
    assert client.get(f"/bug-fab/reports/{rid_a}").get_json()["status"] == "closed"
    assert client.get(f"/bug-fab/reports/{rid_b}").get_json()["status"] == "open"


def test_bulk_archive_closed_returns_count(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """``POST /bulk-archive-closed`` returns ``{archived: N}`` per protocol."""
    _, client = _make_app(tmp_path=tmp_path)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    client.put(f"/bug-fab/reports/{rid}/status", json={"status": "closed"})
    resp = client.post("/bug-fab/bulk-archive-closed")
    assert resp.status_code == 200
    assert "archived" in resp.get_json()


# -----------------------------------------------------------------------------
# HTML viewer + static bundle
# -----------------------------------------------------------------------------


def test_list_html_renders(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """The viewer's HTML list page renders with at least one row."""
    _, client = _make_app(tmp_path=tmp_path)
    _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.get("/bug-fab/")
    assert resp.status_code == 200
    assert b"Bug Reports" in resp.data


def test_detail_html_renders(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """The viewer's HTML detail page renders with the report id present."""
    _, client = _make_app(tmp_path=tmp_path)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)
    resp = client.get(f"/bug-fab/{rid}")
    assert resp.status_code == 200
    assert rid.encode() in resp.data


def test_static_bundle_is_served(tmp_path: Path) -> None:
    """``/bug-fab/static/bug-fab.js`` returns the vendored bundle."""
    _, client = _make_app(tmp_path=tmp_path)
    resp = client.get("/bug-fab/static/bug-fab.js")
    assert resp.status_code == 200
    assert b"BugFab" in resp.data


# -----------------------------------------------------------------------------
# GitHub sync wiring (audit F-1) — adapter must call create_issue on intake
# and sync_issue_state on status update; failures must NOT roll back saves.
# -----------------------------------------------------------------------------


class _FakeGitHubSync:
    """Test-double honoring the GitHubSync surface without HTTP."""

    def __init__(self, *, fail_create: bool = False, fail_state: bool = False) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.state_calls: list[tuple[int, str]] = []
        self._fail_create = fail_create
        self._fail_state = fail_state

    async def create_issue(self, report: dict[str, Any]) -> tuple[int | None, str | None]:
        self.create_calls.append(report)
        if self._fail_create:
            raise RuntimeError("simulated GitHub create failure")
        return 42, f"https://example.invalid/issues/{42}"

    async def sync_issue_state(self, issue_number: int, status_value: str) -> None:
        self.state_calls.append((issue_number, status_value))
        if self._fail_state:
            raise RuntimeError("simulated GitHub state failure")


def _make_app_with_sync(
    *,
    tmp_path: Path,
    sync: _FakeGitHubSync,
) -> tuple[flask.Flask, flask.testing.FlaskClient]:
    """Build an app with the fake sync injected via make_blueprint."""
    settings = Settings(storage_dir=tmp_path / "bug_reports")
    storage = FileStorage(storage_dir=settings.storage_dir)
    app = flask.Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024
    app.register_blueprint(
        make_blueprint(settings, storage=storage, github_sync=sync),
        url_prefix="/bug-fab",
    )
    return app, app.test_client()


def test_intake_calls_github_create_and_persists_link(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Submitting with sync wired calls ``create_issue`` and stores the URL."""
    sync = _FakeGitHubSync()
    _, client = _make_app_with_sync(tmp_path=tmp_path, sync=sync)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)

    assert len(sync.create_calls) == 1
    assert sync.create_calls[0]["id"] == rid

    detail = client.get(f"/bug-fab/reports/{rid}").get_json()
    assert detail["github_issue_url"] == "https://example.invalid/issues/42"
    assert detail["github_issue_number"] == 42


def test_intake_github_failure_still_persists_report(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """A raising ``create_issue`` must not break the 201 response."""
    sync = _FakeGitHubSync(fail_create=True)
    _, client = _make_app_with_sync(tmp_path=tmp_path, sync=sync)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)

    detail = client.get(f"/bug-fab/reports/{rid}").get_json()
    assert detail["github_issue_url"] is None


def test_status_update_calls_github_state_sync(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Status PUT triggers ``sync_issue_state`` when a GH issue exists."""
    sync = _FakeGitHubSync()
    _, client = _make_app_with_sync(tmp_path=tmp_path, sync=sync)
    rid = _seed_one(client, valid_metadata_dict, tiny_png)

    resp = client.put(f"/bug-fab/reports/{rid}/status", json={"status": "fixed"})
    assert resp.status_code == 200
    assert sync.state_calls == [(42, "fixed")]


# -----------------------------------------------------------------------------
# Rate limiter wiring (audit F-2) — opt-in via Settings.rate_limit_enabled.
# Mirrors FastAPI submit.py:167-174.
# -----------------------------------------------------------------------------


def test_rate_limiter_allows_under_threshold(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Submissions under the limit succeed normally."""
    settings = Settings(
        storage_dir=tmp_path / "bug_reports",
        rate_limit_enabled=True,
        rate_limit_max=3,
        rate_limit_window_seconds=60,
    )
    storage = FileStorage(storage_dir=settings.storage_dir)
    app = flask.Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024
    app.register_blueprint(make_blueprint(settings, storage=storage), url_prefix="/bug-fab")
    client = app.test_client()
    for _ in range(3):
        rid = _seed_one(client, valid_metadata_dict, tiny_png)
        assert rid.startswith("bug-")


def test_rate_limiter_blocks_over_threshold_with_429_envelope(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """The (N+1)th submission within the window returns 429 + envelope."""
    settings = Settings(
        storage_dir=tmp_path / "bug_reports",
        rate_limit_enabled=True,
        rate_limit_max=2,
        rate_limit_window_seconds=60,
    )
    storage = FileStorage(storage_dir=settings.storage_dir)
    app = flask.Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024
    app.register_blueprint(make_blueprint(settings, storage=storage), url_prefix="/bug-fab")
    client = app.test_client()

    for _ in range(2):
        _seed_one(client, valid_metadata_dict, tiny_png)

    resp = client.post(
        "/bug-fab/bug-reports",
        data={
            "metadata": json.dumps(valid_metadata_dict),
            "screenshot": (BytesIO(tiny_png), "shot.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 429
    body = resp.get_json()
    assert body["error"] == "rate_limited"
    assert "Rate limit exceeded" in body["detail"]
    assert body["retry_after_seconds"] == 60


def test_status_update_no_state_sync_when_no_issue_number(
    tmp_path: Path,
    tiny_png: bytes,
    valid_metadata_dict: dict[str, Any],
) -> None:
    """Reports without a GH issue number skip the state sync silently."""
    # No GitHubSync wired at all — intake skips create, status update skips sync.
    settings = Settings(storage_dir=tmp_path / "bug_reports")
    storage = FileStorage(storage_dir=settings.storage_dir)
    app = flask.Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024
    app.register_blueprint(make_blueprint(settings, storage=storage), url_prefix="/bug-fab")
    client = app.test_client()
    rid = _seed_one(client, valid_metadata_dict, tiny_png)

    resp = client.put(f"/bug-fab/reports/{rid}/status", json={"status": "fixed"})
    assert resp.status_code == 200
