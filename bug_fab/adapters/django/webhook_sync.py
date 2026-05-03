"""Best-effort generic webhook delivery for the Django adapter.

Mirrors :mod:`bug_fab.integrations.webhook` but uses :class:`httpx.Client`
(the synchronous flavor) instead of :class:`httpx.AsyncClient` because
plain Django views are sync. Failures are logged and swallowed; an
outage downstream MUST NOT cause the local intake response to be
non-2xx (per ``docs/PROTOCOL.md`` § Failure modes that MUST NOT yield
non-2xx).

Configuration via four env vars (Django settings overrides take
precedence when present)::

    BUG_FAB_WEBHOOK_ENABLED            # truthy literal turns the feature on
    BUG_FAB_WEBHOOK_URL                # full destination URL
    BUG_FAB_WEBHOOK_HEADERS            # JSON or "k=v;k2=v2" format
    BUG_FAB_WEBHOOK_TIMEOUT_SECONDS    # default 5.0

Sync is enabled when both ``BUG_FAB_WEBHOOK_ENABLED`` is truthy and
``BUG_FAB_WEBHOOK_URL`` resolves to a non-empty string.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_TRUTHY = {"1", "true", "yes", "on"}


def _setting(name: str, default: str = "") -> str:
    """Resolve a setting from Django then env, returning ``default`` on miss."""
    try:
        from django.conf import settings  # type: ignore[import-not-found]

        value = getattr(settings, name, None)
        if isinstance(value, str) and value:
            return value
    except Exception:  # pragma: no cover - defensive
        pass
    return os.environ.get(name, default)


def _enabled() -> bool:
    """Return ``True`` when the webhook is configured AND opt-in is set."""
    flag = _setting("BUG_FAB_WEBHOOK_ENABLED", "").strip().lower()
    return flag in _TRUTHY and bool(_setting("BUG_FAB_WEBHOOK_URL"))


def _resolve_headers() -> dict[str, str]:
    """Decode the ``BUG_FAB_WEBHOOK_HEADERS`` setting into a header dict."""
    # Reuse the canonical parser so the env-var format stays identical
    # across all three adapters (FastAPI, Flask, Django).
    from bug_fab.integrations.webhook import parse_headers_env

    raw = _setting("BUG_FAB_WEBHOOK_HEADERS", "")
    headers = parse_headers_env(raw)
    headers.setdefault("Content-Type", "application/json")
    return headers


def _resolve_timeout() -> float:
    """Read the per-request timeout from settings/env, defaulting to 5.0s."""
    raw = _setting("BUG_FAB_WEBHOOK_TIMEOUT_SECONDS", "")
    if not raw:
        return 5.0
    try:
        return float(raw)
    except ValueError:
        return 5.0


def send(report: dict[str, Any]) -> bool:
    """POST ``report`` (a JSON-mode BugReportDetail dump) to the webhook URL.

    No-ops with ``False`` when the integration is disabled. On a 2xx
    response returns ``True``; on transport errors, timeouts, or non-2xx
    responses returns ``False`` after a structured log entry. Callers
    MUST treat ``False`` as a soft signal — the local persistence flow
    has already succeeded by the time this is called.
    """
    if not _enabled():
        return False
    try:
        import httpx  # imported lazily — avoids surprising the consumer
    except ImportError:  # pragma: no cover - httpx is a hard dep
        logger.warning("bug_fab_django_webhook_httpx_missing")
        return False
    url = _setting("BUG_FAB_WEBHOOK_URL")
    headers = _resolve_headers()
    timeout = _resolve_timeout()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=report, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(
            "bug_fab_django_webhook_send_error",
            extra={"report_id": report.get("id"), "url": url, "error": str(exc)},
        )
        return False
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_webhook_send_unexpected_error")
        return False
    if response.status_code // 100 != 2:
        body = response.text
        if len(body) > 200:
            body = body[:197] + "..."
        logger.warning(
            "bug_fab_django_webhook_send_failed",
            extra={
                "report_id": report.get("id"),
                "url": url,
                "status_code": response.status_code,
                "body": body,
            },
        )
        return False
    return True
