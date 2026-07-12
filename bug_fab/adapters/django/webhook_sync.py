"""Best-effort generic webhook delivery for the Django adapter.

A thin synchronous wrapper over :class:`bug_fab.integrations.webhook.WebhookSync`
— the same delivery engine the FastAPI and Flask adapters use, so retry,
backoff, dead-letter persistence, and non-2xx classification behave
identically across all three Python adapters. Plain Django views are sync,
so the async ``send`` is driven with :func:`asyncio.run`. Failures are
logged and swallowed; an outage downstream MUST NOT cause the local
intake response to be non-2xx (per ``docs/PROTOCOL.md`` § Failure modes
that MUST NOT yield non-2xx).

Configuration (Django settings overrides take precedence over env)::

    BUG_FAB_WEBHOOK_ENABLED                # truthy literal turns the feature on
    BUG_FAB_WEBHOOK_URL                    # full destination URL
    BUG_FAB_WEBHOOK_HEADERS                # JSON or "k=v;k2=v2" format
    BUG_FAB_WEBHOOK_TIMEOUT_SECONDS        # default 5.0
    BUG_FAB_WEBHOOK_MAX_ATTEMPTS           # default per the shared engine
    BUG_FAB_WEBHOOK_RETRY_BACKOFF_SECONDS  # default per the shared engine
    BUG_FAB_WEBHOOK_DLQ_DIR                # unset disables the dead-letter queue

Sync is enabled when both ``BUG_FAB_WEBHOOK_ENABLED`` is truthy and
``BUG_FAB_WEBHOOK_URL`` resolves to a non-empty string.
"""

from __future__ import annotations

import asyncio
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

    return parse_headers_env(_setting("BUG_FAB_WEBHOOK_HEADERS", ""))


def _resolve_float(name: str, default: float) -> float:
    raw = _setting(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _resolve_int(name: str, default: int) -> int:
    raw = _setting(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def send(report: dict[str, Any]) -> bool:
    """POST ``report`` (a JSON-mode BugReportDetail dump) to the webhook URL.

    No-ops with ``False`` when the integration is disabled. Delegates to
    the shared :class:`~bug_fab.integrations.webhook.WebhookSync`, so a
    transient failure retries with backoff and — when
    ``BUG_FAB_WEBHOOK_DLQ_DIR`` is set — exhausted envelopes land in the
    dead-letter queue for later replay. Returns ``True`` only on a 2xx
    delivery; callers MUST treat ``False`` as a soft signal — the local
    persistence flow has already succeeded by the time this is called.
    """
    if not _enabled():
        return False
    from bug_fab.integrations.webhook import (
        DEFAULT_MAX_ATTEMPTS,
        DEFAULT_RETRY_BACKOFF_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
        WebhookSync,
    )

    sync = WebhookSync(
        _setting("BUG_FAB_WEBHOOK_URL"),
        headers=_resolve_headers(),
        timeout_seconds=_resolve_float("BUG_FAB_WEBHOOK_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        max_attempts=_resolve_int("BUG_FAB_WEBHOOK_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
        retry_backoff_seconds=_resolve_float(
            "BUG_FAB_WEBHOOK_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS
        ),
        dlq_dir=_setting("BUG_FAB_WEBHOOK_DLQ_DIR") or None,
    )
    try:
        # Plain Django views run sync (WSGI, or ASGI's sync-view threadpool),
        # so no event loop is running in this thread and asyncio.run is safe.
        return asyncio.run(sync.send(report))
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_webhook_send_unexpected_error")
        return False
