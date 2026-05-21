"""Optional Microsoft Teams incoming-webhook delivery for Bug-Fab.

Thin convenience wrapper that transforms a ``BugReportDetail``-shaped
payload into a Microsoft Teams Adaptive Card message and POSTs it to a
Teams incoming-webhook URL. Same best-effort failure-tolerance contract
as :class:`bug_fab.integrations.webhook.WebhookSync` — a Teams outage
NEVER blocks intake.

This adapter satisfies the same ``.send(report) -> bool`` contract the
router expects from a webhook sync, so consumers wire it through the
existing ``webhook_sync`` slot of :func:`bug_fab.routers.submit.configure`
— no router changes required. That means a consumer wanting BOTH a
generic JSON webhook AND a Teams post must pick one slot, or compose
their own dispatcher; the slot is intentionally a single hook to keep
the intake path simple.

Wiring patterns:

1. Manual (explicit URL in Python)::

    from bug_fab.integrations.teams import TeamsSync
    from bug_fab.routers import submit

    teams = TeamsSync(
        webhook_url="https://outlook.office.com/webhook/...",
        viewer_base_url="https://bugs.example.com/admin/bug-reports",
    )
    submit.configure(storage=storage, webhook_sync=teams)

2. Env-var driven::

    from bug_fab.integrations.teams import TeamsSync
    from bug_fab.routers import submit

    submit.configure(storage=storage, webhook_sync=TeamsSync.from_env())

   Reads ``BUG_FAB_TEAMS_ENABLED`` (boolean),
   ``BUG_FAB_TEAMS_WEBHOOK_URL`` (required when enabled),
   ``BUG_FAB_TEAMS_VIEWER_BASE_URL`` (optional; renders an OpenUrl
   "View report" button on the card),
   ``BUG_FAB_TEAMS_TIMEOUT_SECONDS`` (optional, default 5.0). Returns
   ``None`` when disabled or unconfigured so passing it directly into
   ``submit.configure(webhook_sync=...)`` is a no-op.

Message shape: a Teams ``message`` envelope wrapping a single Adaptive
Card attachment (``contentType: application/vnd.microsoft.card.adaptive``).
The card body has a severity-mapped colored title TextBlock, a
truncated description TextBlock, a FactSet with reporter / status /
optional environment / optional module, and a subtle context line with
the report id and timestamp. When ``viewer_base_url`` is set, an
``Action.OpenUrl`` button labelled "View report" is appended; the
``actions`` array is OMITTED entirely otherwise because some Teams
clients reject an empty actions list.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Per-request timeout default. Teams' incoming-webhook endpoint
#: normally responds in well under a second; the cap keeps a slow Teams
#: from stretching the intake path. Configurable per-instance and via
#: ``BUG_FAB_TEAMS_TIMEOUT_SECONDS``.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Cap on description body in the Teams message. Adaptive Cards tolerate
#: long TextBlocks but the channel noise-floor argument is the same as
#: for Slack: long bug descriptions belong in the viewer, not in a
#: pinged channel. Truncated text gets an ellipsis suffix.
MAX_DESCRIPTION_CHARS = 500

#: Adaptive Card schema version pinned for the rendered payload.
#: ``1.4`` is broadly supported across desktop / mobile / web Teams
#: clients as of 2026; ``1.5+`` exists but uptake lags. Bump only when
#: a real consumer hits a feature gap.
ADAPTIVE_CARD_VERSION = "1.4"

#: Severity → Adaptive Card TextBlock ``color`` token. The Adaptive
#: Card schema constrains TextBlock colors to a fixed vocabulary:
#: ``default | dark | light | accent | good | warning | attention``.
#: That's a much smaller palette than Slack's free-form hex, so the
#: mapping leans on the semantic tokens — ``attention`` (red-ish) for
#: critical, ``warning`` (orange/yellow) for high, ``accent`` (blue)
#: for medium, ``good`` (green) for low.
SEVERITY_COLORS: dict[str, str] = {
    "critical": "attention",
    "high": "warning",
    "medium": "accent",
    "low": "good",
}

#: Color used when the severity field is missing or carries a value
#: outside the locked vocabulary. ``default`` renders as the client's
#: regular text color — neutral, no semantic claim.
DEFAULT_COLOR = "default"


def _truncate(text: str, limit: int) -> str:
    """Hard-cap ``text`` at ``limit`` chars, appending an ellipsis on overflow."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class TeamsSync:
    """Microsoft Teams incoming-webhook adapter satisfying the router's ``.send`` contract.

    Mirrors the public shape of
    :class:`bug_fab.integrations.slack.SlackSync` and
    :class:`bug_fab.integrations.webhook.WebhookSync`. Construction is
    intentionally cheap (no I/O) so consumers can wire one at startup
    even when the integration ends up unused at request time.
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        viewer_base_url: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = webhook_url
        # Strip the trailing slash once so the per-report link builder
        # doesn't have to think about it. The empty-string case (no
        # viewer link) is preserved as-is.
        self._viewer_base_url = viewer_base_url.rstrip("/") if viewer_base_url else ""
        self._timeout = timeout_seconds

    @property
    def url(self) -> str:
        """Teams incoming-webhook URL (read-only)."""
        return self._url

    @property
    def viewer_base_url(self) -> str:
        """Optional viewer base URL used to render an OpenUrl action per report."""
        return self._viewer_base_url

    @classmethod
    def from_env(cls) -> TeamsSync | None:
        """Build a :class:`TeamsSync` from ``BUG_FAB_TEAMS_*`` env vars.

        Returns ``None`` when the integration is disabled or the
        webhook URL is missing — so the result can be passed directly
        into ``submit.configure(webhook_sync=...)`` without a guard.
        """
        enabled = os.environ.get("BUG_FAB_TEAMS_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        url = os.environ.get("BUG_FAB_TEAMS_WEBHOOK_URL", "").strip()
        if not url:
            logger.warning(
                "bug_fab_teams_from_env_missing_url",
                extra={"reason": "BUG_FAB_TEAMS_WEBHOOK_URL unset"},
            )
            return None
        viewer = os.environ.get("BUG_FAB_TEAMS_VIEWER_BASE_URL", "").strip()
        timeout_raw = os.environ.get("BUG_FAB_TEAMS_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        return cls(url, viewer_base_url=viewer, timeout_seconds=timeout)

    def build_payload(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Render a Bug-Fab report payload as a Teams Adaptive Card message.

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

        header_text = f"{severity_raw.upper()}: {title}"
        color = SEVERITY_COLORS.get(severity_raw, DEFAULT_COLOR)

        # FactSet facts — reporter + status always render, environment
        # and module only when non-empty. Adaptive Card FactSets with
        # empty values render as ugly blank rows, so suppress them.
        facts: list[dict[str, str]] = [
            {"title": "Reporter", "value": reporter_name},
            {"title": "Status", "value": status},
        ]
        if environment:
            facts.append({"title": "Environment", "value": environment})
        if module:
            facts.append({"title": "Module", "value": module})

        # Subtle context line — id and timestamp, joined the same way
        # the Slack adapter does for visual parity across integrations.
        context_parts: list[str] = []
        if report_id:
            context_parts.append(report_id)
        if created_at:
            context_parts.append(created_at)
        context_text = " · ".join(context_parts) if context_parts else "—"

        body: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "text": header_text,
                "size": "Large",
                "weight": "Bolder",
                "color": color,
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": body_text,
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": facts,
            },
            {
                "type": "TextBlock",
                "text": context_text,
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
            },
        ]

        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": ADAPTIVE_CARD_VERSION,
            "body": body,
        }

        # Only attach the actions array when we actually have one — some
        # Teams clients reject an empty ``actions: []`` field.
        if self._viewer_base_url and report_id:
            card["actions"] = [
                {
                    "type": "Action.OpenUrl",
                    "title": "View report",
                    "url": f"{self._viewer_base_url}/{report_id}",
                }
            ]

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }

    async def send(self, report: Mapping[str, Any]) -> bool:
        """POST the rendered Teams Adaptive Card to the webhook URL.

        Returns ``True`` on a 2xx response from Teams, ``False`` on
        transport errors, timeouts, or non-2xx HTTP responses. Never
        raises — exceptions are caught and logged so a failing Teams
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
                "bug_fab_teams_send_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "error": str(exc),
                },
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_teams_send_unexpected_error",
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
                "bug_fab_teams_send_failed",
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
    "ADAPTIVE_CARD_VERSION",
    "DEFAULT_COLOR",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_DESCRIPTION_CHARS",
    "SEVERITY_COLORS",
    "TeamsSync",
]
