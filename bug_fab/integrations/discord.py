"""Optional Discord incoming-webhook delivery for Bug-Fab.

Thin convenience wrapper that transforms a ``BugReportDetail``-shaped
payload into a Discord embed and POSTs it to a Discord webhook URL.
Same best-effort failure-tolerance contract as
:class:`bug_fab.integrations.webhook.WebhookSync` — a Discord outage
NEVER blocks intake.

This adapter satisfies the same ``.send(report) -> bool`` contract the
router expects from a webhook sync, so consumers wire it through the
existing ``webhook_sync`` slot of :func:`bug_fab.routers.submit.configure`
— no router changes required. A consumer wanting BOTH a generic JSON
webhook AND a Discord post must pick one slot or compose their own
dispatcher; the slot is intentionally a single hook to keep the intake
path simple.

Wiring patterns:

1. Manual (explicit URL in Python)::

    from bug_fab.integrations.discord import DiscordSync
    from bug_fab.routers import submit

    discord = DiscordSync(
        webhook_url="https://discord.com/api/webhooks/.../...",
        viewer_base_url="https://bugs.example.com/admin/bug-reports",
    )
    submit.configure(storage=storage, webhook_sync=discord)

2. Env-var driven::

    from bug_fab.integrations.discord import DiscordSync
    from bug_fab.routers import submit

    submit.configure(storage=storage, webhook_sync=DiscordSync.from_env())

   Reads ``BUG_FAB_DISCORD_ENABLED`` (boolean),
   ``BUG_FAB_DISCORD_WEBHOOK_URL`` (required when enabled),
   ``BUG_FAB_DISCORD_VIEWER_BASE_URL`` (optional; makes the embed title
   clickable), ``BUG_FAB_DISCORD_TIMEOUT_SECONDS`` (optional, default
   5.0), and ``BUG_FAB_DISCORD_USERNAME`` (optional override, default
   ``Bug-Fab``). Returns ``None`` when disabled or unconfigured so
   passing it directly into ``submit.configure(webhook_sync=...)`` is
   a no-op.

Message shape: a single Discord embed with a severity-mapped sidebar
color (Discord encodes embed colors as a decimal RGB integer, NOT a
hex string the way Slack does), a header-style title (severity +
title), description body (truncated for channel hygiene), four inline
fields (reporter / status / environment / module), a footer carrying
the report id and timestamp, and an optional ``url`` that makes the
title a clickable link to the consumer's viewer.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Per-request timeout default. Discord's webhook endpoint normally
#: responds in well under one second; the cap keeps a slow Discord from
#: stretching the intake path. Configurable per-instance and via
#: ``BUG_FAB_DISCORD_TIMEOUT_SECONDS``.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Cap on description body in the Discord embed. Discord allows up to
#: 4096 chars in an embed description, but anything past ~500 turns
#: into a wall of text in a channel — long descriptions belong in the
#: viewer, not in the channel notification. Same cap as the Slack
#: adapter for cross-channel consistency.
MAX_DESCRIPTION_CHARS = 500

#: Default bot username shown on the message. Discord webhooks let the
#: sender override the username per request; we always set one so
#: messages render as ``Bug-Fab`` rather than as the webhook's bound
#: channel-app name (which varies by how the consumer created the
#: webhook). Overridable via ``BUG_FAB_DISCORD_USERNAME``.
DEFAULT_USERNAME = "Bug-Fab"

#: Embed color per severity level, encoded as a decimal RGB integer.
#: Discord requires the color field to be an integer (not a hex
#: string), so the Bootstrap-style palette the Slack adapter ships as
#: ``#dc3545`` / ``#fd7e14`` / etc. gets converted to its decimal form
#: here. The values are pinned (rather than computed) so a reader can
#: grep for the exact integer that lands in a captured payload.
SEVERITY_COLORS: dict[str, int] = {
    "critical": 14431029,  # #DC3545
    "high": 16613668,  # #FD7E14
    "medium": 16766720,  # #FFC107
    "low": 904063,  # #0D6EFD
}

#: Color used when the severity field is missing or carries a value
#: outside the locked vocabulary (gray = unknown / drop-down deprecated).
#: Decimal form of ``#6c757d``.
DEFAULT_COLOR = 7102291


def _truncate(text: str, limit: int) -> str:
    """Hard-cap ``text`` at ``limit`` chars, appending an ellipsis on overflow."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class DiscordSync:
    """Discord incoming-webhook adapter satisfying the router's ``.send`` contract.

    Mirrors the public shape of
    :class:`bug_fab.integrations.slack.SlackSync`. Construction is
    intentionally cheap (no I/O) so consumers can wire one at startup
    even when the integration ends up unused at request time.
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        viewer_base_url: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        username: str = DEFAULT_USERNAME,
    ) -> None:
        self._url = webhook_url
        # Strip the trailing slash once so the per-report link builder
        # doesn't have to think about it. The empty-string case (no
        # viewer link) is preserved as-is.
        self._viewer_base_url = viewer_base_url.rstrip("/") if viewer_base_url else ""
        self._timeout = timeout_seconds
        self._username = username or DEFAULT_USERNAME

    @property
    def url(self) -> str:
        """Discord incoming-webhook URL (read-only)."""
        return self._url

    @property
    def viewer_base_url(self) -> str:
        """Optional viewer base URL used to render a clickable embed title."""
        return self._viewer_base_url

    @property
    def username(self) -> str:
        """Bot username shown on the posted message."""
        return self._username

    @classmethod
    def from_env(cls) -> DiscordSync | None:
        """Build a :class:`DiscordSync` from ``BUG_FAB_DISCORD_*`` env vars.

        Returns ``None`` when the integration is disabled or the
        webhook URL is missing — so the result can be passed directly
        into ``submit.configure(webhook_sync=...)`` without a guard.
        """
        enabled = os.environ.get("BUG_FAB_DISCORD_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        url = os.environ.get("BUG_FAB_DISCORD_WEBHOOK_URL", "").strip()
        if not url:
            logger.warning(
                "bug_fab_discord_from_env_missing_url",
                extra={"reason": "BUG_FAB_DISCORD_WEBHOOK_URL unset"},
            )
            return None
        viewer = os.environ.get("BUG_FAB_DISCORD_VIEWER_BASE_URL", "").strip()
        timeout_raw = os.environ.get("BUG_FAB_DISCORD_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        username = os.environ.get("BUG_FAB_DISCORD_USERNAME", "").strip() or DEFAULT_USERNAME
        return cls(
            url,
            viewer_base_url=viewer,
            timeout_seconds=timeout,
            username=username,
        )

    def build_payload(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Render a Bug-Fab report payload as a Discord webhook message.

        Public so consumers can preview the rendered shape (handy for
        tests and for sketching custom dispatchers that route to other
        channels off the same transform).
        """
        report_id = str(report.get("id") or "")
        title = str(report.get("title") or "(no title)")
        severity_raw = str(report.get("severity") or "medium").lower()
        status = str(report.get("status") or "open").lower()
        description = str(report.get("description") or "")
        environment = str(report.get("environment") or "")
        module = str(report.get("module") or "")
        created_at = str(report.get("created_at") or "")

        reporter_raw = report.get("reporter")
        if isinstance(reporter_raw, Mapping):
            reporter_name = str(reporter_raw.get("name") or "").strip() or "anonymous"
        else:
            reporter_name = "anonymous"

        body_text = (
            _truncate(description, MAX_DESCRIPTION_CHARS) if description else "_(no description)_"
        )

        embed_title = f"{severity_raw.upper()}: {title}"
        color = SEVERITY_COLORS.get(severity_raw, DEFAULT_COLOR)

        # Discord field names cap at 256 chars and values at 1024; the
        # short-form values here (reporter name, status, env, module)
        # never approach those caps in practice.
        fields: list[dict[str, Any]] = [
            {"name": "Reporter", "value": reporter_name, "inline": True},
            {"name": "Status", "value": status, "inline": True},
        ]
        if environment:
            fields.append({"name": "Environment", "value": environment, "inline": True})
        if module:
            fields.append({"name": "Module", "value": module, "inline": True})

        footer_parts: list[str] = []
        if report_id:
            footer_parts.append(report_id)
        if created_at:
            footer_parts.append(created_at)
        footer_text = " · ".join(footer_parts) if footer_parts else "—"

        embed: dict[str, Any] = {
            "title": embed_title,
            "description": body_text,
            "color": color,
            "fields": fields,
            "footer": {"text": footer_text},
        }
        if self._viewer_base_url and report_id:
            embed["url"] = f"{self._viewer_base_url}/{report_id}"

        return {
            "username": self._username,
            "embeds": [embed],
        }

    async def send(self, report: Mapping[str, Any]) -> bool:
        """POST the rendered Discord message to the webhook URL.

        Returns ``True`` on a 2xx response from Discord, ``False`` on
        transport errors, timeouts, or non-2xx HTTP responses. Never
        raises — exceptions are caught and logged so a failing Discord
        delivery cannot block the intake response path.
        """
        payload = self.build_payload(report)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "bug_fab_discord_send_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "error": str(exc),
                },
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_discord_send_unexpected_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "error": str(exc),
                },
            )
            return False
        if resp.status_code // 100 != 2:
            body = resp.text
            if len(body) > 200:
                body = body[:197] + "..."
            logger.warning(
                "bug_fab_discord_send_failed",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "status_code": resp.status_code,
                    "body": body,
                },
            )
            return False
        return True


__all__ = [
    "DEFAULT_COLOR",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USERNAME",
    "MAX_DESCRIPTION_CHARS",
    "SEVERITY_COLORS",
    "DiscordSync",
]
