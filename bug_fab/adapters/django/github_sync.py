"""Best-effort GitHub Issues sync for the Django adapter.

A standalone module so the views layer can import it lazily — consumers
without GitHub credentials never load :mod:`requests`. Failures are
logged and swallowed; an outage upstream MUST NOT cause the local
intake response to be non-2xx (per ``docs/PROTOCOL.md`` § Failure modes
that MUST NOT yield non-2xx).

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


def _post_issue(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Send the create-issue request, returning the parsed JSON on 2xx."""
    import requests  # imported lazily — optional dep at runtime

    repo = _setting("BUG_FAB_GITHUB_REPO")
    pat = _setting("BUG_FAB_GITHUB_PAT")
    api_base = _setting("BUG_FAB_GITHUB_API_BASE", "https://api.github.com")
    url = f"{api_base.rstrip('/')}/repos/{repo}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code // 100 != 2:
        logger.warning(
            "bug_fab_django_github_create_failed",
            extra={"status": response.status_code, "body": response.text[:500]},
        )
        return None
    return response.json()


def create_issue(report: dict[str, Any]) -> GitHubLink | None:
    """Create a GitHub issue for ``report``, or return ``None`` on failure.

    The ``report`` mapping is the JSON-mode dump of
    :class:`bug_fab.schemas.BugReportDetail`. Non-2xx responses, missing
    config, or import-time failures all surface as ``None`` after a
    structured log entry — callers MUST treat absence as a soft signal.
    """
    if not _enabled():
        return None
    title = report.get("title", "Bug report")
    body_lines = [
        f"**ID:** {report.get('id')}",
        f"**Severity:** {report.get('severity')}",
        f"**Environment:** {report.get('environment') or '(unspecified)'}",
        "",
        report.get("description", "") or "(no description)",
    ]
    payload = {
        "title": f"[Bug-Fab] {title}",
        "body": "\n".join(body_lines),
    }
    try:
        result = _post_issue(payload)
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_github_create_exception")
        return None
    if not result:
        return None
    number = result.get("number")
    url = result.get("html_url")
    if not (isinstance(number, int) and isinstance(url, str)):
        return None
    return GitHubLink(number=number, url=url)


def sync_issue_state(issue_number: int, status: str) -> None:
    """Patch an existing issue's open/closed state from a Bug-Fab status.

    ``fixed`` and ``closed`` close the upstream issue; ``open`` and
    ``investigating`` reopen it. Failures log and swallow.
    """
    if not _enabled():
        return
    try:
        import requests  # imported lazily

        repo = _setting("BUG_FAB_GITHUB_REPO")
        pat = _setting("BUG_FAB_GITHUB_PAT")
        api_base = _setting("BUG_FAB_GITHUB_API_BASE", "https://api.github.com")
        url = f"{api_base.rstrip('/')}/repos/{repo}/issues/{issue_number}"
        state = "closed" if status in ("fixed", "closed") else "open"
        response = requests.patch(
            url,
            json={"state": state},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {pat}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        if response.status_code // 100 != 2:
            logger.warning(
                "bug_fab_django_github_state_sync_failed",
                extra={"status": response.status_code, "body": response.text[:500]},
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_github_state_sync_exception")
