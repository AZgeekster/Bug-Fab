"""Integration tests for CSP-nonce stamping on the viewer's inline scripts.

The viewer ships three inline ``<script>`` blocks (one in ``_base.html``,
one in ``list.html``, one in ``detail.html``). When the consumer wires a
``csp_nonce_provider`` callable into ``Settings``, every block must
render with a matching ``nonce="..."`` attribute so a strict
``Content-Security-Policy: script-src 'nonce-XYZ'`` header allows them.
When no provider is configured, templates render without the attribute
(full back-compat with consumers that have no CSP or that allow
``'unsafe-inline'``).
"""

from __future__ import annotations

import json
import re
from typing import Any


def _baseline_metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "protocol_version": "0.1",
        "title": "CSP nonce test report",
        "client_ts": "2026-04-29T12:00:00+00:00",
        "report_type": "bug",
        "description": "csp nonce seed",
        "severity": "medium",
        "tags": ["csp-test"],
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


def _seed(client, tiny_png: bytes) -> str:
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _vp(client) -> str:
    return getattr(client, "viewer_prefix", "")


# -----------------------------------------------------------------------------
# Back-compat: no provider -> no nonce attribute on any script tag
# -----------------------------------------------------------------------------


def test_list_view_omits_nonce_when_provider_unset(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    _seed(client, tiny_png)
    response = client.get(_vp(client) or "/")
    assert response.status_code == 200
    # Every <script> opening tag must carry no nonce attribute.
    script_tags = re.findall(r"<script[^>]*>", response.text)
    assert script_tags, "expected at least one inline <script> tag"
    for tag in script_tags:
        assert "nonce=" not in tag, f"unexpected nonce on tag: {tag}"


def test_detail_view_omits_nonce_when_provider_unset(app_factory, tiny_png: bytes) -> None:
    client = app_factory()
    bid = _seed(client, tiny_png)
    response = client.get(f"{_vp(client)}/{bid}")
    assert response.status_code == 200
    script_tags = re.findall(r"<script[^>]*>", response.text)
    assert script_tags
    for tag in script_tags:
        assert "nonce=" not in tag, f"unexpected nonce on tag: {tag}"


# -----------------------------------------------------------------------------
# Strict-CSP path: provider supplies a nonce -> every inline script carries it
# -----------------------------------------------------------------------------


def test_list_view_stamps_nonce_when_provider_returns_value(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    settings = settings_factory(csp_nonce_provider=lambda _req: "abc123XYZ")
    client = app_factory(settings=settings)
    _seed(client, tiny_png)
    response = client.get(_vp(client) or "/")
    assert response.status_code == 200
    script_tags = re.findall(r"<script[^>]*>", response.text)
    assert script_tags
    for tag in script_tags:
        assert 'nonce="abc123XYZ"' in tag, f"missing nonce on tag: {tag}"


def test_detail_view_stamps_nonce_when_provider_returns_value(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    settings = settings_factory(csp_nonce_provider=lambda _req: "detailNonce42")
    client = app_factory(settings=settings)
    bid = _seed(client, tiny_png)
    response = client.get(f"{_vp(client)}/{bid}")
    assert response.status_code == 200
    script_tags = re.findall(r"<script[^>]*>", response.text)
    assert script_tags
    for tag in script_tags:
        assert 'nonce="detailNonce42"' in tag, f"missing nonce on tag: {tag}"


# -----------------------------------------------------------------------------
# Provider returning None -> render without nonce (per-request opt-out)
# -----------------------------------------------------------------------------


def test_list_view_omits_nonce_when_provider_returns_none(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    settings = settings_factory(csp_nonce_provider=lambda _req: None)
    client = app_factory(settings=settings)
    _seed(client, tiny_png)
    response = client.get(_vp(client) or "/")
    assert response.status_code == 200
    for tag in re.findall(r"<script[^>]*>", response.text):
        assert "nonce=" not in tag


# -----------------------------------------------------------------------------
# Per-request nonce uniqueness — the provider sees the live Request object
# -----------------------------------------------------------------------------


def test_provider_receives_request_object(app_factory, settings_factory, tiny_png: bytes) -> None:
    """The provider gets called with the FastAPI Request, not a placeholder."""
    seen: list[Any] = []

    def provider(request) -> str:
        seen.append(type(request).__name__)
        return "ok"

    settings = settings_factory(csp_nonce_provider=provider)
    client = app_factory(settings=settings)
    _seed(client, tiny_png)
    response = client.get(_vp(client) or "/")
    assert response.status_code == 200
    assert seen, "provider was never invoked"
    # The exact class name varies across Starlette versions; the contract
    # is just that something Request-shaped reaches the provider.
    assert any("Request" in name for name in seen), f"unexpected types: {seen}"


# -----------------------------------------------------------------------------
# Misbehaving provider must not crash the page
# -----------------------------------------------------------------------------


def test_list_view_falls_back_when_provider_raises(
    app_factory, settings_factory, tiny_png: bytes
) -> None:
    def boom(_req):
        raise RuntimeError("provider exploded")

    settings = settings_factory(csp_nonce_provider=boom)
    client = app_factory(settings=settings)
    _seed(client, tiny_png)
    response = client.get(_vp(client) or "/")
    assert response.status_code == 200
    # Page renders, just without nonce attributes.
    for tag in re.findall(r"<script[^>]*>", response.text):
        assert "nonce=" not in tag


# -----------------------------------------------------------------------------
# Inline onclick was replaced by data-action wiring
# -----------------------------------------------------------------------------


def test_refresh_button_uses_data_action_not_inline_onclick(app_factory, tiny_png: bytes) -> None:
    """Strict CSP forbids inline event handlers; the Refresh button must
    use the data-action / addEventListener pattern instead."""
    client = app_factory()
    _seed(client, tiny_png)
    response = client.get(_vp(client) or "/")
    assert response.status_code == 200
    assert "onclick=" not in response.text, "inline onclick handler must be removed"
    assert 'data-bug-fab-action="reload"' in response.text
