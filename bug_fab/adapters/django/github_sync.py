"""Best-effort GitHub Issues sync for the Django adapter.

A thin synchronous wrapper over :class:`bug_fab.integrations.github.GitHubSync`
— the same client the FastAPI adapter uses, so Django-submitted reports
produce identical issues (title format, labels, body sections) instead of
the divergent minimal shape this module used to build itself. Plain
Django views are sync, so the async client is driven with
:func:`asyncio.run`. Failures are logged and swallowed; an outage
upstream MUST NOT cause the local intake response to be non-2xx (per
``docs/PROTOCOL.md`` § Failure modes that MUST NOT yield non-2xx).

Configuration via three env vars (Django settings overrides take
precedence when present)::

    BUG_FAB_GITHUB_REPO       # owner/repo, e.g. "AZgeekster/Bug-Fab"
    BUG_FAB_GITHUB_PAT        # PAT or fine-grained token
    BUG_FAB_GITHUB_API_BASE   # default: https://api.github.com

Sync is enabled when both ``BUG_FAB_GITHUB_REPO`` and
``BUG_FAB_GITHUB_PAT`` resolve to non-empty strings; otherwise both
helpers no-op so the views can call them unconditionally.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitHubLink:
    """Minimal upstream-issue reference returned by :func:`create_issue`."""

    number: int
    url: str


def _setting(name: str, default: str = "") -> str:
    """Resolve a setting from Django then env, returning ``default`` on miss.

    Imported lazily so the helper still works in test contexts where
    Django settings are not yet configured (the env-var fallback is
    enough for plain pytest test runs).
    """
    try:
        from django.conf import settings  # type: ignore[import-not-found]

        value = getattr(settings, name, None)
        if isinstance(value, str) and value:
            return value
    except Exception:  # pragma: no cover - defensive
        pass
    return os.environ.get(name, default)


def _enabled() -> bool:
    """Return ``True`` when both repo and PAT are configured."""
    return bool(_setting("BUG_FAB_GITHUB_REPO") and _setting("BUG_FAB_GITHUB_PAT"))


def _client() -> Any:
    """Build the shared GitHubSync client from resolved settings."""
    from bug_fab.integrations.github import GitHubSync

    return GitHubSync(
        pat=_setting("BUG_FAB_GITHUB_PAT"),
        repo=_setting("BUG_FAB_GITHUB_REPO"),
        api_base=_setting("BUG_FAB_GITHUB_API_BASE", "https://api.github.com"),
    )


def create_issue(report: dict[str, Any]) -> GitHubLink | None:
    """Create a GitHub issue for ``report``, or return ``None`` on failure.

    The ``report`` mapping is the JSON-mode dump of
    :class:`bug_fab.schemas.BugReportDetail`. Non-2xx responses, missing
    config, or transport failures all surface as ``None`` after a
    structured log entry — callers MUST treat absence as a soft signal.
    """
    if not _enabled():
        return None
    try:
        # Plain Django views run sync (WSGI, or ASGI's sync-view
        # threadpool), so no event loop is running in this thread and
        # asyncio.run is safe.
        number, url = asyncio.run(_client().create_issue(report))
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_github_create_exception")
        return None
    if not (isinstance(number, int) and isinstance(url, str)):
        return None
    return GitHubLink(number=number, url=url)


def sync_issue_state(issue_number: int, status: str) -> None:
    """Patch an existing issue's open/closed state from a Bug-Fab status.

    The shared client's state map decides the upstream state (``fixed``
    and ``closed`` close the issue; other statuses reopen it). Failures
    log and swallow.
    """
    if not _enabled():
        return
    try:
        asyncio.run(_client().sync_issue_state(issue_number, status))
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_github_state_sync_exception")
