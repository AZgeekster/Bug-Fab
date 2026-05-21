"""Optional Slack incoming-webhook delivery for Bug-Fab.

Thin convenience wrapper that transforms a ``BugReportDetail``-shaped
payload into a Slack Block Kit message and POSTs it to a Slack
incoming-webhook URL. Same best-effort failure-tolerance contract as
``WebhookSync`` — a Slack outage NEVER blocks intake.

This adapter satisfies the same ``.send(report) -> bool`` contract the
router expects from a webhook sync, so consumers wire it through the
existing ``webhook_sync`` slot of :func:`bug_fab.routers.submit.configure`
— no router changes required. That means a consumer wanting BOTH a
generic JSON webhook AND a Slack post must pick one slot, or compose
their own dispatcher; the slot is intentionally a single hook to keep
the intake path simple.

Wiring patterns:

1. Manual (explicit URL in Python)::

    from bug_fab.integrations.slack import SlackSync
    from bug_fab.routers import submit

    slack = SlackSync(
        webhook_url="https://hooks.slack.com/services/...",
        viewer_base_url="https://bugs.example.com/admin/bug-reports",
    )
    submit.configure(storage=storage, webhook_sync=slack)

2. Env-var driven::

    from bug_fab.integrations.slack import SlackSync
    from bug_fab.routers import submit

    submit.configure(storage=storage, webhook_sync=SlackSync.from_env())

   Reads ``BUG_FAB_SLACK_ENABLED`` (boolean),
   ``BUG_FAB_SLACK_WEBHOOK_URL`` (required when enabled),
   ``BUG_FAB_SLACK_VIEWER_BASE_URL`` (optional; renders a "View" link),
   ``BUG_FAB_SLACK_TIMEOUT_SECONDS`` (optional, default 5.0). Returns
   ``None`` when disabled or unconfigured so passing it directly into
   ``submit.configure(webhook_sync=...)`` is a no-op.

Message shape: a single Slack ``attachments`` entry with a
severity-mapped color sidebar (one of four documented colors) and four
Block Kit blocks — header (severity + title), section (description,
truncated to keep messages scannable), fields (reporter / status /
environment / module), and a context line with the report id,
timestamp, and optional viewer / GitHub-issue links.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Per-request timeout default. Slack's incoming-webhook endpoint
#: normally responds in under 200 ms; the cap keeps a slow Slack from
#: stretching the intake path. Configurable per-instance and via
#: ``BUG_FAB_SLACK_TIMEOUT_SECONDS``.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Cap on description body in the Slack message. Slack section text
#: degrades past ~3000 chars, but a smaller cap keeps the channel
#: noise-floor low — long bug descriptions belong in the viewer, not
#: in a pinged channel. Truncated text gets an ellipsis suffix.
MAX_DESCRIPTION_CHARS = 500

#: Attachment color per severity level. Slack legacy-attachment colors
#: render as a colored left edge on the message — the cheapest way to
#: signal severity at a glance. Bootstrap-style hex codes for cross-
#: client recognisability (Bootstrap, GitHub, Tailwind all converge on
#: similar palettes for danger/warning/info).
SEVERITY_COLORS: dict[str, str] = {
    "critical": "#dc3545",
    "high": "#fd7e14",
    "medium": "#ffc107",
    "low": "#0d6efd",
}

#: Color used when the severity field is missing or carries a value
#: outside the locked vocabulary (gray = unknown / drop-down deprecated).
DEFAULT_COLOR = "#6c757d"


def _truncate(text: str, limit: int) -> str:
    """Hard-cap ``text`` at ``limit`` chars, appending an ellipsis on overflow."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class SlackSync:
    """Slack incoming-webhook adapter satisfying the router's ``.send`` contract.

    Mirrors the public shape of
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
        """Slack incoming-webhook URL (read-only)."""
        return self._url

    @property
    def viewer_base_url(self) -> str:
        """Optional viewer base URL used to render a "View" link per report."""
        return self._viewer_base_url

    @classmethod
    def from_env(cls) -> SlackSync | None:
        """Build a :class:`SlackSync` from ``BUG_FAB_SLACK_*`` env vars.

        Returns ``None`` when the integration is disabled or the
        webhook URL is missing — so the result can be passed directly
        into ``submit.configure(webhook_sync=...)`` without a guard.
        """
        enabled = os.environ.get("BUG_FAB_SLACK_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        url = os.environ.get("BUG_FAB_SLACK_WEBHOOK_URL", "").strip()
        if not url:
            logger.warning(
                "bug_fab_slack_from_env_missing_url",
                extra={"reason": "BUG_FAB_SLACK_WEBHOOK_URL unset"},
            )
            return None
        viewer = os.environ.get("BUG_FAB_SLACK_VIEWER_BASE_URL", "").strip()
        timeout_raw = os.environ.get("BUG_FAB_SLACK_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        return cls(url, viewer_base_url=viewer, timeout_seconds=timeout)

    def build_payload(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Render a Bug-Fab report payload as a Slack Block Kit message.

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
        github_issue_url = report.get("github_issue_url")

        reporter_raw = report.get("reporter")
        if isinstance(reporter_raw, Mapping):
            reporter_name = str(reporter_raw.get("name") or "").strip() or "anonymous"
        else:
            reporter_name = "anonymous"

        body_text = (
            _truncate(description, MAX_DESCRIPTION_CHARS) if description else "_(no description)_"
        )

        header_text = f"{severity_raw.upper()}: {title}"

        context_parts: list[str] = []
        if report_id:
            context_parts.append(f"`{report_id}`")
        if created_at:
            context_parts.append(created_at)
        if self._viewer_base_url and report_id:
            context_parts.append(f"<{self._viewer_base_url}/{report_id}|View>")
        if isinstance(github_issue_url, str) and github_issue_url:
            context_parts.append(f"<{github_issue_url}|GitHub issue>")
        context_text = " · ".join(context_parts) if context_parts else "—"

        fields: list[dict[str, str]] = [
            {"type": "mrkdwn", "text": f"*Reporter:*\n{reporter_name}"},
            {"type": "mrkdwn", "text": f"*Status:*\n{status}"},
        ]
        if environment:
            fields.append({"type": "mrkdwn", "text": f"*Environment:*\n{environment}"})
        if module:
            fields.append({"type": "mrkdwn", "text": f"*Module:*\n{module}"})

        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": header_text}},
            {"type": "section", "text": {"type": "mrkdwn", "text": body_text}},
            {"type": "section", "fields": fields},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]},
        ]

        color = SEVERITY_COLORS.get(severity_raw, DEFAULT_COLOR)
        return {
            "attachments": [
                {
                    # `fallback` is what Slack clients that don't render
                    # blocks (mobile notifications, screen-reader mode)
                    # show in lieu of the structured payload.
                    "fallback": header_text,
                    "color": color,
                    "blocks": blocks,
                }
            ]
        }

    async def send(self, report: Mapping[str, Any]) -> bool:
        """POST the rendered Slack message to the webhook URL.

        Returns ``True`` on a 2xx response from Slack, ``False`` on
        transport errors, timeouts, or non-2xx HTTP responses. Never
        raises — exceptions are caught and logged so a failing Slack
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
                "bug_fab_slack_send_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "error": str(exc),
                },
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_slack_send_unexpected_error",
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
                "bug_fab_slack_send_failed",
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
    "MAX_DESCRIPTION_CHARS",
    "SEVERITY_COLORS",
    "SlackSync",
]
