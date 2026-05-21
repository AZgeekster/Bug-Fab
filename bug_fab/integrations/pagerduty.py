"""Optional PagerDuty Events API v2 escalation for Bug-Fab.

Thin adapter that escalates *critical* bug reports to PagerDuty as
incidents via the public Events API v2 endpoint
(``https://events.pagerduty.com/v2/enqueue``). Mirrors the
``.send(report) -> bool`` contract :class:`bug_fab.integrations.slack.SlackSync`
and :class:`bug_fab.integrations.webhook.WebhookSync` expose, so the
adapter wires through the same ``webhook_sync`` slot of
:func:`bug_fab.routers.submit.configure`.

PagerDuty is paging / on-call escalation rather than chat notification —
firing the on-call pager for every low-severity UI quirk is hostile to
the people carrying that pager. The adapter is therefore **selective by
default**: only reports whose ``severity`` is in
``escalate_severities`` actually call PagerDuty; everything else is
suppressed and the call returns ``False`` (a documented suppression
signal, not a failure). Suppressions log at DEBUG so an operator can
audit what was and wasn't paged.

Wiring patterns:

1. Manual (explicit integration key in Python)::

    from bug_fab.integrations.pagerduty import PagerDutySync
    from bug_fab.routers import submit

    pd = PagerDutySync(
        integration_key="R0UTING_KEY_FROM_PAGERDUTY",
        viewer_base_url="https://bugs.example.com/admin/bug-reports",
    )
    submit.configure(storage=storage, webhook_sync=pd)

2. Env-var driven::

    submit.configure(storage=storage, webhook_sync=PagerDutySync.from_env())

   Reads ``BUG_FAB_PAGERDUTY_ENABLED`` (boolean),
   ``BUG_FAB_PAGERDUTY_INTEGRATION_KEY`` (required when enabled),
   ``BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES`` (comma-separated, default
   ``"critical"``),
   ``BUG_FAB_PAGERDUTY_API_URL`` (override of the public endpoint, for
   testing or PagerDuty EU tenants),
   ``BUG_FAB_PAGERDUTY_VIEWER_BASE_URL`` (optional; renders a link in
   the incident's ``links[]`` array AND in ``custom_details``),
   ``BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS`` (default 10.0),
   ``BUG_FAB_PAGERDUTY_DEDUP_PREFIX`` (default ``"bug-fab"``). Returns
   ``None`` when disabled or unconfigured so passing it directly into
   ``submit.configure(webhook_sync=...)`` is a no-op.

Wire shape: a single ``POST`` to the Events API v2 enqueue endpoint
with a JSON body containing ``routing_key``, ``event_action="trigger"``,
a ``dedup_key`` of the form ``<prefix>-<report_id>`` (re-submits of the
same report collapse into one open incident — useful when an upstream
retries a webhook delivery), and a ``payload`` block whose
``severity`` is the PagerDuty-vocabulary mapping of the Bug-Fab
severity. Events API v2 accepts the trigger and returns **202 Accepted**
(not 200) — the adapter treats any 2xx as success but the typical
success status code is 202.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Default per-request timeout in seconds. PagerDuty's Events API v2
#: enqueue endpoint normally responds in well under a second, but a
#: 10-second cap allows for transient global slowdowns without
#: stretching the Bug-Fab intake path forever. Configurable per
#: instance and via ``BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS``.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Public Events API v2 endpoint. The legacy v1 endpoint
#: (``/generic/.../create_event.json``) is deprecated; v2 has a richer
#: payload model (severity vocab, ``dedup_key``, ``links``, structured
#: ``custom_details``) so the adapter targets it exclusively. EU
#: tenants can override via ``BUG_FAB_PAGERDUTY_API_URL``.
DEFAULT_API_URL = "https://events.pagerduty.com/v2/enqueue"

#: Default dedup-key prefix. PagerDuty collapses incidents that share
#: a ``dedup_key`` into a single open incident — so a retried webhook
#: never pages twice for the same Bug-Fab report. Configurable per
#: instance and via ``BUG_FAB_PAGERDUTY_DEDUP_PREFIX``.
DEFAULT_DEDUP_PREFIX = "bug-fab"

#: Severities escalated to PagerDuty by default. Critical-only is the
#: documented opinionated default — most consumers do NOT want their
#: on-call paged for every UI nit. Override per instance with the
#: ``escalate_severities`` constructor argument or via the
#: ``BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES`` env var.
DEFAULT_ESCALATE_SEVERITIES: tuple[str, ...] = ("critical",)

#: Cap on the description body copied into ``custom_details``. PagerDuty
#: stores the full payload but its UI truncates aggressively in list
#: views — keeping the wire payload small avoids noisy mobile
#: notifications for bug reports with novel-length descriptions.
MAX_DESCRIPTION_CHARS = 1024

#: Maps Bug-Fab's severity vocabulary to PagerDuty's Events API v2
#: severity vocabulary. PagerDuty accepts exactly four values:
#: ``critical``, ``error``, ``warning``, ``info``. Bug-Fab's ``high``
#: maps to ``error`` (PagerDuty has no ``high``); anything unknown or
#: missing maps to ``info`` so a malformed report still pages cleanly
#: rather than triggering a 400 from the Events API.
SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high": "error",
    "medium": "warning",
    "low": "info",
}

#: PagerDuty severity used when the inbound report's ``severity`` is
#: missing or outside the Bug-Fab vocabulary.
DEFAULT_PAGERDUTY_SEVERITY = "info"


def _truncate(text: str, limit: int) -> str:
    """Hard-cap ``text`` at ``limit`` chars, appending an ellipsis on overflow."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_severities(values: Any) -> tuple[str, ...]:
    """Coerce an iterable / string of severities into a lower-case tuple.

    Tolerant of accidental whitespace and mixed case from env-var
    sources. Empty entries are dropped so a trailing comma in the env
    var doesn't smuggle in an empty string severity.
    """
    if isinstance(values, str):
        items: list[str] = [v.strip().lower() for v in values.split(",")]
    else:
        items = [str(v).strip().lower() for v in values]
    return tuple(item for item in items if item)


class PagerDutySync:
    """PagerDuty Events API v2 adapter satisfying the router's ``.send`` contract.

    Construction is intentionally cheap (no I/O) so a consumer can wire
    one at startup even when the integration ends up unused at request
    time. ``send`` performs the only outbound call.

    Parameters
    ----------
    integration_key:
        The Events API v2 "integration key" (a.k.a. "routing key") that
        PagerDuty's UI hands out per service. Required; the Events API
        rejects requests without it.
    escalate_severities:
        Tuple of Bug-Fab severities (``"critical"``, ``"high"``,
        ``"medium"``, ``"low"``) that should actually page PagerDuty.
        Anything outside this tuple is suppressed and ``send`` returns
        ``False`` without making a network call. Default is
        ``("critical",)`` — see module docstring.
    api_url:
        Override of the default Events API v2 enqueue endpoint. Useful
        for PagerDuty EU tenants or for swapping in a test double.
    viewer_base_url:
        Optional Bug-Fab viewer base URL. When set, a deep link to the
        report is rendered into ``payload.custom_details.viewer_url``
        AND added to the incident's ``links[]`` array so the on-call
        engineer can jump straight to the report from the PagerDuty
        mobile app.
    timeout_seconds:
        Per-request timeout passed to the underlying
        :class:`httpx.AsyncClient`.
    dedup_prefix:
        Prefix used to build ``dedup_key`` (``<prefix>-<report_id>``).
        Override when running multiple Bug-Fab instances against a
        single PagerDuty service to keep their dedup namespaces
        separate.
    """

    def __init__(
        self,
        *,
        integration_key: str,
        escalate_severities: tuple[str, ...] = DEFAULT_ESCALATE_SEVERITIES,
        api_url: str = DEFAULT_API_URL,
        viewer_base_url: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        dedup_prefix: str = DEFAULT_DEDUP_PREFIX,
    ) -> None:
        self._integration_key = integration_key
        self._escalate_severities = _normalize_severities(escalate_severities)
        self._api_url = api_url
        # Strip the trailing slash once so per-report URL builders don't
        # have to think about it. Empty-string preserved (no viewer link).
        self._viewer_base_url = viewer_base_url.rstrip("/") if viewer_base_url else ""
        self._timeout = timeout_seconds
        self._dedup_prefix = dedup_prefix or DEFAULT_DEDUP_PREFIX

    @property
    def integration_key(self) -> str:
        """PagerDuty integration / routing key (read-only)."""
        return self._integration_key

    @property
    def escalate_severities(self) -> tuple[str, ...]:
        """Severities that trigger an actual page (read-only)."""
        return self._escalate_severities

    @property
    def api_url(self) -> str:
        """Events API v2 enqueue URL in use (read-only)."""
        return self._api_url

    @property
    def viewer_base_url(self) -> str:
        """Optional viewer base URL used to render incident links."""
        return self._viewer_base_url

    @property
    def dedup_prefix(self) -> str:
        """Prefix used to construct ``dedup_key`` (read-only)."""
        return self._dedup_prefix

    @classmethod
    def from_env(cls) -> PagerDutySync | None:
        """Build a :class:`PagerDutySync` from ``BUG_FAB_PAGERDUTY_*`` env vars.

        Returns ``None`` when the integration is disabled or the
        integration key is missing — so the result can be passed
        directly into ``submit.configure(webhook_sync=...)`` without a
        guard.
        """
        enabled = os.environ.get("BUG_FAB_PAGERDUTY_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        integration_key = os.environ.get("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", "").strip()
        if not integration_key:
            logger.warning(
                "bug_fab_pagerduty_from_env_missing_integration_key",
                extra={"reason": "BUG_FAB_PAGERDUTY_INTEGRATION_KEY unset"},
            )
            return None
        severities_raw = os.environ.get("BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES", "").strip()
        if severities_raw:
            escalate = _normalize_severities(severities_raw)
            if not escalate:
                # A pathological value like ``","`` decodes to no
                # entries — fall back to the documented default rather
                # than silently suppressing everything.
                escalate = DEFAULT_ESCALATE_SEVERITIES
        else:
            escalate = DEFAULT_ESCALATE_SEVERITIES
        api_url = os.environ.get("BUG_FAB_PAGERDUTY_API_URL", "").strip() or DEFAULT_API_URL
        viewer = os.environ.get("BUG_FAB_PAGERDUTY_VIEWER_BASE_URL", "").strip()
        timeout_raw = os.environ.get("BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        dedup_prefix = (
            os.environ.get("BUG_FAB_PAGERDUTY_DEDUP_PREFIX", "").strip() or DEFAULT_DEDUP_PREFIX
        )
        return cls(
            integration_key=integration_key,
            escalate_severities=escalate,
            api_url=api_url,
            viewer_base_url=viewer,
            timeout_seconds=timeout,
            dedup_prefix=dedup_prefix,
        )

    def build_payload(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Render a Bug-Fab report payload as a PagerDuty Events API v2 body.

        Public so consumers can preview the rendered shape (handy for
        tests and for sketching custom dispatchers that route to other
        Events-API integrations off the same transform).
        """
        report_id = str(report.get("id") or "")
        title = str(report.get("title") or "(no title)")
        severity_raw = str(report.get("severity") or "medium").lower()
        description = str(report.get("description") or "")
        environment = str(report.get("environment") or "")
        module = str(report.get("module") or "")
        created_at = str(report.get("created_at") or "")

        reporter_raw = report.get("reporter")
        if isinstance(reporter_raw, Mapping):
            reporter_name = str(reporter_raw.get("name") or "").strip() or "anonymous"
        else:
            reporter_name = "anonymous"

        pd_severity = SEVERITY_MAP.get(severity_raw, DEFAULT_PAGERDUTY_SEVERITY)
        summary = f"{severity_raw.upper()}: {title}"

        custom_details: dict[str, Any] = {
            "report_id": report_id,
            "reporter": reporter_name,
            "description": _truncate(description, MAX_DESCRIPTION_CHARS) if description else "",
            "created_at": created_at,
        }
        if self._viewer_base_url and report_id:
            custom_details["viewer_url"] = f"{self._viewer_base_url}/{report_id}"

        payload: dict[str, Any] = {
            "summary": summary,
            "source": "bug-fab",
            "severity": pd_severity,
            "custom_details": custom_details,
        }
        # `component` and `group` are optional Events-API v2 fields that
        # PagerDuty surfaces in incident list views — populate when the
        # report has the data, omit otherwise rather than sending empty
        # strings (which PagerDuty does accept, but render as noise).
        if module:
            payload["component"] = module
        if environment:
            payload["group"] = environment

        body: dict[str, Any] = {
            "routing_key": self._integration_key,
            "event_action": "trigger",
            "dedup_key": f"{self._dedup_prefix}-{report_id}" if report_id else self._dedup_prefix,
            "payload": payload,
        }
        if self._viewer_base_url and report_id:
            body["links"] = [
                {
                    "href": f"{self._viewer_base_url}/{report_id}",
                    "text": "View in Bug-Fab viewer",
                }
            ]
        return body

    async def send(self, report: Mapping[str, Any]) -> bool:
        """Trigger a PagerDuty incident for ``report`` when its severity escalates.

        Returns ``False`` *without* calling PagerDuty when the report's
        severity isn't in ``escalate_severities`` — this is the
        documented suppression behavior, not a failure. The suppression
        is logged at DEBUG so an operator can audit which reports were
        intentionally dropped.

        On a 2xx response from the Events API v2 endpoint (typically
        ``202 Accepted``), returns ``True``. On non-2xx, transport
        error, or timeout, logs at WARN and returns ``False`` — a
        PagerDuty outage must NEVER block intake.
        """
        severity = str(report.get("severity") or "medium").lower()
        if severity not in self._escalate_severities:
            logger.debug(
                "bug_fab_pagerduty_suppressed",
                extra={
                    "report_id": report.get("id"),
                    "severity": severity,
                    "escalate_severities": list(self._escalate_severities),
                },
            )
            return False

        body = self.build_payload(report)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._api_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "bug_fab_pagerduty_send_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._api_url,
                    "error": str(exc),
                },
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_pagerduty_send_unexpected_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._api_url,
                    "error": str(exc),
                },
            )
            return False
        if resp.status_code // 100 != 2:
            body_text = resp.text
            if len(body_text) > 200:
                body_text = body_text[:197] + "..."
            logger.warning(
                "bug_fab_pagerduty_send_failed",
                extra={
                    "report_id": report.get("id"),
                    "url": self._api_url,
                    "status_code": resp.status_code,
                    "body": body_text,
                },
            )
            return False
        return True


__all__ = [
    "DEFAULT_API_URL",
    "DEFAULT_DEDUP_PREFIX",
    "DEFAULT_ESCALATE_SEVERITIES",
    "DEFAULT_PAGERDUTY_SEVERITY",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_DESCRIPTION_CHARS",
    "SEVERITY_MAP",
    "PagerDutySync",
]
