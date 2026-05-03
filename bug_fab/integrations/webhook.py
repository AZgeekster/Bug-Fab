"""Optional generic webhook delivery for Bug-Fab.

Best-effort POST to a consumer-configured URL after every new report
saves locally. Failures are logged at WARN and never raise into the
intake response path. Designed to plug Bug-Fab into any service that
accepts a JSON body — Slack incoming-webhooks, Linear project webhooks,
Pushover, n8n / Zapier triggers, a custom internal collector, etc.

This module is loaded only when a consumer enables webhook integration
in their :class:`bug_fab.config.Settings`. Every external call is best-
effort — a failed webhook request is logged and ignored so the local
submission flow always succeeds, mirroring the same failure-tolerance
contract the GitHub Issues sync uses (see
:mod:`bug_fab.integrations.github`).

Wire shape: a single JSON request body equal to
``BugReportDetail.model_dump(mode="json")`` so consumers receive the
full persisted payload (id, title, severity, status, lifecycle, plus
the ``github_issue_url`` if a separate GitHub sync fired first). A
``Content-Type: application/json`` header is always sent; consumers can
add :class:`Authorization` (or any other) header via the ``headers``
constructor argument or the ``BUG_FAB_WEBHOOK_HEADERS`` env var.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Default per-request timeout in seconds. Webhook receivers (Slack,
#: Linear, n8n) typically respond well under one second; the cap keeps
#: a slow downstream from stretching the intake path. Configurable via
#: the ``timeout_seconds`` constructor argument or the
#: ``BUG_FAB_WEBHOOK_TIMEOUT_SECONDS`` env var.
DEFAULT_TIMEOUT_SECONDS = 5.0


def parse_headers_env(raw: str | None) -> dict[str, str]:
    """Decode a ``BUG_FAB_WEBHOOK_HEADERS`` env-var value into a header dict.

    Accepts two formats so consumers can pick whichever survives their
    deployment tooling's quoting:

    1. JSON object — ``{"Authorization": "Bearer xyz"}``. The canonical
       form, recommended for anything beyond a single header.
    2. Semicolon-separated ``key=value`` pairs —
       ``Authorization=Bearer xyz;X-Source=bug-fab``. Easier to set in
       a shell or a .env file when the value has no awkward characters.

    Empty / unset / unparseable values resolve to ``{}`` so a malformed
    env var never crashes the process at import time.
    """
    if not raw:
        return {}
    text = raw.strip()
    if not text:
        return {}
    # Try JSON first — the canonical form. A leading "{" is a strong
    # signal so we don't waste a parse attempt on shell-pair values.
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("bug_fab_webhook_headers_env_invalid_json")
            return {}
        if not isinstance(decoded, dict):
            return {}
        return {str(k): str(v) for k, v in decoded.items() if k}
    # Fallback: ``key=value;key2=value2`` shell-pair form. Whitespace
    # around the separator is tolerated; empty pairs are skipped.
    headers: dict[str, str] = {}
    for pair in text.split(";"):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            headers[key] = value
    return headers


class WebhookSync:
    """Async client for the generic webhook delivery feature.

    Parameters
    ----------
    url:
        The full HTTP/HTTPS URL the consumer wants every new report
        POSTed to. No trailing-slash normalization — the URL is used
        verbatim so consumers retain control over query strings and
        path-token authentication.
    headers:
        Extra HTTP headers added to every outbound request. Typically
        an ``Authorization`` token or a custom ``X-Bug-Fab-Source``
        marker. ``Content-Type`` defaults to ``application/json`` and
        can be overridden through this mapping.
    timeout_seconds:
        Per-request timeout passed to the underlying
        :class:`httpx.AsyncClient`. Failures (timeout, connection
        refused, 4xx, 5xx) all log at WARN and return ``False``.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = url
        # Always send JSON content-type unless the consumer explicitly
        # overrides it; merge consumer headers on top so a custom
        # ``Content-Type`` (rare, but possible for some collectors) wins.
        merged: dict[str, str] = {"Content-Type": "application/json"}
        if headers:
            merged.update({str(k): str(v) for k, v in headers.items()})
        self._headers = merged
        self._timeout = timeout_seconds

    @property
    def url(self) -> str:
        """The destination URL (read-only outside of construction)."""
        return self._url

    @property
    def headers(self) -> dict[str, str]:
        """Headers used on every outbound webhook POST."""
        return dict(self._headers)

    async def send(self, report: Mapping[str, Any]) -> bool:
        """POST the report payload to the configured webhook URL.

        ``report`` is expected to be the JSON-mode dump of
        :class:`bug_fab.schemas.BugReportDetail` — the same shape the
        viewer's ``GET /reports/{id}`` endpoint emits. Any
        JSON-serializable mapping is accepted; the method does not
        re-validate the structure because the caller has already
        round-tripped it through Pydantic.

        Returns ``True`` on a 2xx response, ``False`` on transport
        errors, timeouts, or non-2xx HTTP responses. The call always
        returns — exceptions are caught and logged so a failing
        webhook never blocks the intake response path.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=dict(report), headers=self._headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "bug_fab_webhook_send_error",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "error": str(exc),
                },
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_webhook_send_unexpected_error",
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
                "bug_fab_webhook_send_failed",
                extra={
                    "report_id": report.get("id"),
                    "url": self._url,
                    "status_code": resp.status_code,
                    "body": body,
                },
            )
            return False
        return True


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "WebhookSync", "parse_headers_env"]
