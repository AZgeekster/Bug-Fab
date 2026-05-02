"""Integration tests for the Bug-Fab Django adapter.

Drives every view through Django's :class:`~django.test.Client` so the
URL routing, model ORM, multipart parser, and JSON serialization paths
all run end-to-end. Mirrors the FastAPI integration suite's coverage:
intake happy-path, validation rejections, viewer list / detail,
screenshot serve, status update + lifecycle append, delete, and bulk
operations.
"""

from __future__ import annotations

import json

from django.core.files.uploadedfile import SimpleUploadedFile


def _post_intake(client, metadata_json: str, png_bytes: bytes, **kwargs):
    """Submit a multipart intake request, returning the Django response."""
    return client.post(
        "/bug-reports",
        data={
            "metadata": metadata_json,
            "screenshot": SimpleUploadedFile("screenshot.png", png_bytes, content_type="image/png"),
        },
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Intake — happy path + validation
# ---------------------------------------------------------------------------


def test_intake_happy_path(client, metadata_json, png_bytes):
    response = _post_intake(client, metadata_json, png_bytes)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["id"].startswith("bug-")
    assert body["received_at"]
    assert body["stored_at"].startswith("bug-fab-django://")
    assert body["github_issue_url"] is None


def test_intake_persists_user_agent_from_request_header(client, metadata_json, png_bytes):
    response = _post_intake(
        client, metadata_json, png_bytes, HTTP_USER_AGENT="Mozilla/5.0 (server-captured)"
    )
    assert response.status_code == 201
    rid = response.json()["id"]
    detail = client.get(f"/reports/{rid}").json()
    assert detail["server_user_agent"] == "Mozilla/5.0 (server-captured)"
    # Client-reported value is preserved verbatim from metadata.context.user_agent.
    assert detail["client_reported_user_agent"] == "Mozilla/5.0 (test client)"


def test_intake_rejects_missing_metadata(client, png_bytes):
    response = client.post(
        "/bug-reports",
        data={
            "screenshot": SimpleUploadedFile("s.png", png_bytes, content_type="image/png"),
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_error"


def test_intake_rejects_missing_screenshot(client, metadata_json):
    response = client.post("/bug-reports", data={"metadata": metadata_json})
    assert response.status_code == 400


def test_intake_rejects_non_png_bytes(client, metadata_json):
    response = client.post(
        "/bug-reports",
        data={
            "metadata": metadata_json,
            "screenshot": SimpleUploadedFile(
                "evil.png", b"not really a png", content_type="image/png"
            ),
        },
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"


def test_intake_rejects_invalid_severity(client, png_bytes):
    payload = json.loads(
        '{"protocol_version":"0.1","title":"x","client_ts":"2026-04-27T00:00:00Z","severity":"urgent"}'
    )
    response = _post_intake(client, json.dumps(payload), png_bytes)
    assert response.status_code == 422
    assert response.json()["error"] == "schema_error"


def test_intake_rejects_unknown_protocol_version(client, png_bytes):
    payload = {
        "protocol_version": "9.9",
        "title": "x",
        "client_ts": "2026-04-27T00:00:00Z",
    }
    response = _post_intake(client, json.dumps(payload), png_bytes)
    assert response.status_code == 422


def test_intake_rejects_overlong_reporter(client, png_bytes):
    payload = {
        "protocol_version": "0.1",
        "title": "x",
        "client_ts": "2026-04-27T00:00:00Z",
        "reporter": {"name": "a" * 257},
    }
    response = _post_intake(client, json.dumps(payload), png_bytes)
    assert response.status_code == 422


def test_intake_rejects_invalid_metadata_json(client, png_bytes):
    response = _post_intake(client, "{not-json", png_bytes)
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


# ---------------------------------------------------------------------------
# Viewer — list + detail + screenshot
# ---------------------------------------------------------------------------


def test_list_json_returns_pagination_envelope(client, metadata_json, png_bytes):
    _post_intake(client, metadata_json, png_bytes)
    _post_intake(client, metadata_json, png_bytes)
    response = client.get("/reports")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert len(body["items"]) == 2
    assert "stats" in body
    assert body["stats"]["open"] == 2


def test_list_filters_by_status(client, metadata_json, png_bytes):
    _post_intake(client, metadata_json, png_bytes)
    response = client.get("/reports", {"status": "fixed"})
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_detail_json_returns_full_payload(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.get(f"/reports/{rid}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == rid
    assert body["title"] == "Save button is unresponsive"
    assert body["severity"] == "high"
    assert body["status"] == "open"
    assert body["protocol_version"] == "0.1"
    assert isinstance(body["lifecycle"], list)
    assert body["lifecycle"][0]["action"] == "created"
    assert body["context"]["module"] == "checkout"


def test_detail_404_for_unknown_id(client):
    response = client.get("/reports/bug-999")
    assert response.status_code == 404


def test_detail_404_for_path_traversal(client):
    response = client.get("/reports/..%2Fetc%2Fpasswd")
    assert response.status_code == 404


def test_screenshot_serves_png_bytes(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.get(f"/reports/{rid}/screenshot")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    body = b"".join(response.streaming_content)
    assert body.startswith(b"\x89PNG")


# ---------------------------------------------------------------------------
# Viewer — status update + lifecycle
# ---------------------------------------------------------------------------


def test_status_update_appends_lifecycle(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.put(
        f"/reports/{rid}/status",
        data=json.dumps(
            {"status": "fixed", "fix_commit": "abc123", "fix_description": "Restored handler."}
        ),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "fixed"
    actions = [entry["action"] for entry in body["lifecycle"]]
    assert "created" in actions
    assert "status_changed" in actions
    last = body["lifecycle"][-1]
    assert last["action"] == "status_changed"
    assert last["fix_commit"] == "abc123"


def test_status_update_rejects_unknown_status(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.put(
        f"/reports/{rid}/status",
        data=json.dumps({"status": "bogus"}),
        content_type="application/json",
    )
    assert response.status_code == 422


def test_status_update_404_for_missing_report(client):
    response = client.put(
        "/reports/bug-999/status",
        data=json.dumps({"status": "fixed"}),
        content_type="application/json",
    )
    assert response.status_code == 404


def test_status_update_rejects_invalid_json_body(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.put(
        f"/reports/{rid}/status", data="not-json", content_type="application/json"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Viewer — delete + bulk
# ---------------------------------------------------------------------------


def test_delete_returns_204(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.delete(f"/reports/{rid}")
    assert response.status_code == 204
    # Subsequent GET is 404.
    assert client.get(f"/reports/{rid}").status_code == 404


def test_delete_404_for_missing_report(client):
    response = client.delete("/reports/bug-999")
    assert response.status_code == 404


def test_bulk_close_fixed_transitions_only_fixed(client, metadata_json, png_bytes):
    rid_a = _post_intake(client, metadata_json, png_bytes).json()["id"]
    rid_b = _post_intake(client, metadata_json, png_bytes).json()["id"]
    # Move A to fixed, leave B as open.
    client.put(
        f"/reports/{rid_a}/status",
        data=json.dumps({"status": "fixed"}),
        content_type="application/json",
    )
    response = client.post("/bulk-close-fixed")
    assert response.status_code == 200
    assert response.json()["closed"] == 1
    # Verify A is now closed, B is still open.
    assert client.get(f"/reports/{rid_a}").json()["status"] == "closed"
    assert client.get(f"/reports/{rid_b}").json()["status"] == "open"


def test_bulk_archive_closed_archives_only_closed(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    client.put(
        f"/reports/{rid}/status",
        data=json.dumps({"status": "closed"}),
        content_type="application/json",
    )
    response = client.post("/bulk-archive-closed")
    assert response.status_code == 200
    assert response.json()["archived"] == 1
    # Default list excludes archived.
    assert client.get("/reports").json()["total"] == 0


# ---------------------------------------------------------------------------
# HTML viewer pages
# ---------------------------------------------------------------------------


def test_list_html_renders(client, metadata_json, png_bytes):
    _post_intake(client, metadata_json, png_bytes)
    response = client.get("/")
    assert response.status_code == 200
    assert b"Bug Reports" in response.content


def test_detail_html_renders(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.get(f"/{rid}")
    assert response.status_code == 200
    assert rid.encode() in response.content
    assert b"Save button is unresponsive" in response.content


# ---------------------------------------------------------------------------
# Bundle view
# ---------------------------------------------------------------------------


def test_bundle_view_serves_javascript(client):
    response = client.get("/bug-fab/static/bug-fab.js")
    # 200 if package is installed editable + bundle is on disk; 404 in
    # the rare case the bundle was stripped (pure-source install).
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response["Content-Type"] == "application/javascript"


# ---------------------------------------------------------------------------
# Method enforcement
# ---------------------------------------------------------------------------


def test_intake_rejects_get(client):
    response = client.get("/bug-reports")
    assert response.status_code == 405


def test_status_update_rejects_post(client, metadata_json, png_bytes):
    rid = _post_intake(client, metadata_json, png_bytes).json()["id"]
    response = client.post(f"/reports/{rid}/status")
    assert response.status_code == 405
