"""Integration tests for the optional Discord incoming-webhook adapter.

External HTTP is captured via :class:`httpx.MockTransport` — same
harness shape ``test_slack_integration`` uses. The tests verify
that:

* ``DiscordSync.send`` POSTs a Discord webhook payload (username +
  embeds[]) to the configured URL with ``Content-Type: application/json``.
* The severity field on the report maps to the documented integer
  colors (Discord encodes embed colors as decimal RGB, not hex strings
  the way Slack does); unknown severities fall back to a neutral
  default rather than crashing.
* The viewer-base-url, when set, populates the embed ``url`` field so
  the title is clickable.
* Long descriptions are truncated with an ellipsis to keep channels
  scannable.
* Non-2xx responses, transport errors, and timeouts ALL return
  ``False`` rather than raising — preserving the failure-tolerance
  contract that Discord outages must NOT block intake.
* ``DiscordSync.from_env`` reads ``BUG_FAB_DISCORD_*`` env vars and
  returns ``None`` when disabled or unconfigured, so it can be passed
  straight into ``submit.configure(webhook_sync=...)``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from bug_fab.integrations.discord import (
    DEFAULT_COLOR,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USERNAME,
    MAX_DESCRIPTION_CHARS,
    SEVERITY_COLORS,
    DiscordSync,
)
from tests._helpers import (
    clear_env_prefix,
    install_capturing_async_client,
)
from tests._helpers import (
    decode_json_body as _decode_body,
)
from tests._helpers import (
    make_report_detail as _make_report,
)
from tests._helpers import (
    run_coro as _run,
)


@pytest.fixture(autouse=True)
def _hermetic_discord_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip pre-existing ``BUG_FAB_DISCORD_*`` vars so ``from_env`` tests are hermetic.

    The tests used to ``delenv`` only the variables they named and failed
    spuriously on machines exporting other ``BUG_FAB_DISCORD_*`` values.
    """
    clear_env_prefix(monkeypatch, "BUG_FAB_DISCORD_")


def _make_sync_with_transport(
    handler: Any,
    *,
    url: str = "https://discord.com/api/webhooks/123456789/abcdefghijklmnop",
    viewer_base_url: str = "",
    timeout_seconds: float = 5.0,
    username: str = DEFAULT_USERNAME,
) -> tuple[DiscordSync, list[httpx.Request]]:
    """Build a DiscordSync whose internal httpx client uses a MockTransport.

    Returns ``(sync, captured_requests)``; the list is appended to in
    order so post-hoc assertions can inspect headers and decoded body.
    """
    captured = install_capturing_async_client(handler)
    sync = DiscordSync(
        url,
        viewer_base_url=viewer_base_url,
        timeout_seconds=timeout_seconds,
        username=username,
    )
    return sync, captured


def test_default_timeout_is_five_seconds() -> None:
    """Pin the default — anything longer would stretch intake under Discord issues."""
    assert DEFAULT_TIMEOUT_SECONDS == 5.0


def test_send_posts_embeds_payload_to_webhook_url() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        assert req.headers["content-type"] == "application/json"
        return httpx.Response(204)

    sync, captured = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is True
    assert len(captured) == 1

    assert "embeds" in captured_payload
    embeds = captured_payload["embeds"]
    assert isinstance(embeds, list) and len(embeds) == 1
    embed = embeds[0]
    assert embed["color"] == SEVERITY_COLORS["critical"]
    assert "CRITICAL" in embed["title"]
    assert "Login button" in embed["title"]


def test_username_is_set_on_outer_payload_not_inside_embed() -> None:
    """Discord requires ``username`` at the top of the body, NOT in the embed."""
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report()))

    assert captured_payload.get("username") == DEFAULT_USERNAME
    embed = captured_payload["embeds"][0]
    assert "username" not in embed


def test_embeds_is_list_with_exactly_one_element() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report()))

    embeds = captured_payload["embeds"]
    assert isinstance(embeds, list)
    assert len(embeds) == 1


@pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
def test_each_severity_maps_to_documented_color(severity: str) -> None:
    """Critical / high / medium / low all use their pinned integer color.

    Parametrized rather than looped because ``_make_sync_with_transport``
    monkey-patches ``httpx.AsyncClient`` and chained patches inside a
    single test compound through the inheritance chain — only the first
    iteration's transport actually fires.
    """
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(severity=severity)))
    color = captured[0]["embeds"][0]["color"]
    assert color == SEVERITY_COLORS[severity]
    assert isinstance(color, int)


def test_unknown_severity_falls_back_to_default_color() -> None:
    """A weirdly-named severity must not crash — graceful fallback to gray."""
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report(severity="catastrophic"))) is True
    assert captured[0]["embeds"][0]["color"] == DEFAULT_COLOR


def test_viewer_base_url_renders_embed_url() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(
        handler, viewer_base_url="https://bugs.example.com/admin/bug-reports"
    )
    _run(sync.send(_make_report(id="bug-042")))

    embed = captured[0]["embeds"][0]
    assert embed["url"] == "https://bugs.example.com/admin/bug-reports/bug-042"


def test_viewer_base_url_trailing_slash_is_stripped() -> None:
    """Constructor normalizes one trailing slash so URLs don't double up."""
    sync = DiscordSync(
        "https://discord.com/api/webhooks/x/y",
        viewer_base_url="https://bugs.example.com/admin/bug-reports/",
    )
    assert sync.viewer_base_url == "https://bugs.example.com/admin/bug-reports"
    payload = sync.build_payload(_make_report(id="bug-042"))
    assert payload["embeds"][0]["url"] == "https://bugs.example.com/admin/bug-reports/bug-042"


def test_embed_url_omitted_when_no_viewer_base_url() -> None:
    """Without a viewer URL the embed has no ``url`` key (Discord ignores empties)."""
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)  # default: no viewer_base_url
    _run(sync.send(_make_report()))

    embed = captured[0]["embeds"][0]
    assert "url" not in embed


def test_long_description_truncated_with_ellipsis() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    long = "x" * (MAX_DESCRIPTION_CHARS + 500)
    _run(sync.send(_make_report(description=long)))

    description = captured[0]["embeds"][0]["description"]
    assert len(description) <= MAX_DESCRIPTION_CHARS
    assert description.endswith("…")


def test_empty_description_renders_placeholder() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(description="")))

    description = captured[0]["embeds"][0]["description"]
    assert "no description" in description


def test_anonymous_reporter_when_name_missing() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(reporter={"name": "", "email": "", "user_id": ""})))

    embed = captured[0]["embeds"][0]
    reporter_field = next(f for f in embed["fields"] if f["name"] == "Reporter")
    assert reporter_field["value"] == "anonymous"
    assert reporter_field["inline"] is True


def test_fields_are_inline_for_compact_row_layout() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(204)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report()))

    fields = captured[0]["embeds"][0]["fields"]
    assert all(f["inline"] is True for f in fields)
    names = [f["name"] for f in fields]
    assert "Reporter" in names
    assert "Status" in names
    assert "Environment" in names
    assert "Module" in names


def test_non_2xx_response_returns_false() -> None:
    """404 (e.g., invalid webhook token) doesn't crash intake."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="invalid_token")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_timeout_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow Discord")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_connect_error_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_build_payload_is_pure_and_idempotent() -> None:
    """No network: callers can inspect the rendered shape directly."""
    sync = DiscordSync("https://discord.com/api/webhooks/x/y")
    report = _make_report()
    a = sync.build_payload(report)
    b = sync.build_payload(report)
    assert a == b
    assert a["embeds"][0]["color"] == SEVERITY_COLORS["critical"]
    assert a["username"] == DEFAULT_USERNAME


def test_from_env_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUG_FAB_DISCORD_ENABLED", raising=False)
    monkeypatch.delenv("BUG_FAB_DISCORD_WEBHOOK_URL", raising=False)
    assert DiscordSync.from_env() is None


def test_from_env_enabled_without_url_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_DISCORD_ENABLED", "true")
    monkeypatch.delenv("BUG_FAB_DISCORD_WEBHOOK_URL", raising=False)
    assert DiscordSync.from_env() is None


def test_from_env_builds_sync_with_all_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_DISCORD_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/A/B")
    monkeypatch.setenv(
        "BUG_FAB_DISCORD_VIEWER_BASE_URL", "https://bugs.example.com/admin/bug-reports"
    )
    monkeypatch.setenv("BUG_FAB_DISCORD_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("BUG_FAB_DISCORD_USERNAME", "QA-Bot")

    sync = DiscordSync.from_env()
    assert sync is not None
    assert sync.url == "https://discord.com/api/webhooks/A/B"
    assert sync.viewer_base_url == "https://bugs.example.com/admin/bug-reports"
    assert sync.username == "QA-Bot"


def test_from_env_invalid_timeout_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in BUG_FAB_DISCORD_TIMEOUT_SECONDS must not break startup."""
    monkeypatch.setenv("BUG_FAB_DISCORD_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/A/B")
    monkeypatch.setenv("BUG_FAB_DISCORD_TIMEOUT_SECONDS", "not-a-number")
    sync = DiscordSync.from_env()
    assert sync is not None  # built despite the typo


def test_from_env_default_username_when_not_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ``BUG_FAB_DISCORD_USERNAME`` resolves to the default ``Bug-Fab``."""
    monkeypatch.setenv("BUG_FAB_DISCORD_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/A/B")
    monkeypatch.delenv("BUG_FAB_DISCORD_USERNAME", raising=False)
    sync = DiscordSync.from_env()
    assert sync is not None
    assert sync.username == DEFAULT_USERNAME
