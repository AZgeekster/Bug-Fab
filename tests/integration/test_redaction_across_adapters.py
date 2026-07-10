"""`BUG_FAB_REDACT_PII` must scrub the persisted report on **every** adapter.

`redact_report` was implemented, documented, and exported — and called from
exactly one place: the FastAPI intake router. An operator who set
`BUG_FAB_REDACT_PII=true` on the Flask or Django adapter got a control that
reported success and did nothing, which is worse than an absent feature: the
raw tokens landed on disk while the deployment believed they had not.

Nothing caught it because every redaction test targeted `_redact` directly,
never an adapter's intake path. These tests assert the *stored* payload, so
they fail if any adapter stops calling the redactor. All three are exercised
in one module on purpose — a per-adapter file is exactly how the gap opened.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

# A JWT-shaped token: three base64url segments, each >= 10 chars.
LEAKED_JWT = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
LEAKED_EMAIL = "victim@example.com"


def _metadata_with_secrets() -> dict[str, Any]:
    """Baseline metadata carrying a token in the console stack and an email in the title."""
    return {
        "protocol_version": "0.1",
        "title": f"Login broke for {LEAKED_EMAIL}",
        "client_ts": "2026-07-10T12:00:00+00:00",
        "report_type": "bug",
        "description": f"Request failed with Authorization: Bearer {LEAKED_JWT}",
        "severity": "high",
        "context": {
            "url": "http://localhost/login",
            "module": "auth",
            "user_agent": "probe/1.0",
            "viewport_width": 1280,
            "viewport_height": 720,
            "console_errors": [
                {"message": "Unauthorized", "stack": f"at auth.js token={LEAKED_JWT}"}
            ],
            "network_log": [],
            "environment": "dev",
        },
    }


def _assert_scrubbed(blob: str, adapter: str) -> None:
    """The raw secrets must not appear anywhere in the persisted JSON."""
    assert LEAKED_JWT not in blob, f"{adapter}: raw JWT persisted despite BUG_FAB_REDACT_PII=true"
    assert LEAKED_EMAIL not in blob, (
        f"{adapter}: raw email persisted despite BUG_FAB_REDACT_PII=true"
    )
    assert "<redacted" in blob, f"{adapter}: nothing was redacted — did the redactor run at all?"


def _stored_blob(storage_dir) -> str:
    """Concatenate every persisted metadata JSON under ``storage_dir``."""
    parts = [p.read_text(encoding="utf-8") for p in storage_dir.rglob("*.json")]
    assert parts, "no report was persisted — the submission did not reach storage"
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# FastAPI (the one adapter that always did this correctly)
# ---------------------------------------------------------------------------


def test_fastapi_redacts_when_enabled(app_factory, settings_factory, tiny_png, file_storage):
    client = app_factory(settings=settings_factory(redact_pii=True))
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_metadata_with_secrets())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    _assert_scrubbed(_stored_blob(file_storage.storage_dir), "fastapi")


def test_fastapi_preserves_raw_text_when_disabled(
    app_factory, settings_factory, tiny_png, file_storage
):
    """Redaction is opt-in. Off by default, the raw text must survive."""
    client = app_factory(settings=settings_factory(redact_pii=False))
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_metadata_with_secrets())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    assert LEAKED_JWT in _stored_blob(file_storage.storage_dir)


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------


def test_flask_redacts_when_enabled(tmp_path, tiny_png):
    pytest.importorskip("flask")
    from flask import Flask

    from bug_fab.adapters.flask import make_blueprint
    from bug_fab.config import Settings
    from bug_fab.storage.files import FileStorage

    storage_dir = tmp_path / "flask-reports"
    storage = FileStorage(storage_dir=storage_dir)
    settings = Settings(redact_pii=True)

    app = Flask(__name__)
    app.register_blueprint(make_blueprint(settings, storage=storage))
    client = app.test_client()

    response = client.post(
        "/bug-reports",
        data={
            "metadata": json.dumps(_metadata_with_secrets()),
            "screenshot": (io.BytesIO(tiny_png), "shot.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    _assert_scrubbed(_stored_blob(storage_dir), "flask")
