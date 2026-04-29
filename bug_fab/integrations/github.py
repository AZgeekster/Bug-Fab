"""Optional GitHub Issues sync for Bug-Fab.

This module is loaded only when a consumer enables GitHub integration in
their ``Settings``. Every external call is best-effort — a failed GitHub
request is logged and ignored so the local submission flow always
succeeds.

Three operations are exposed:

* :meth:`GitHubSync.ensure_labels` — idempotent label seed (one-shot per
  process via an internal flag).
* :meth:`GitHubSync.create_issue` — POST a new issue for a freshly
  submitted bug report.
* :meth:`GitHubSync.sync_issue_state` — PATCH an existing issue's open /
  closed state when a report's status changes.

Both label colors and the status-to-issue-state mapping are
constructor-configurable; the defaults match every prior implementation
audited for v0.1.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Default ``severity:`` and ``env:`` label palette. Hex values match every
#: audited prior implementation so consumers porting from an existing
#: deployment get visually identical labels.
DEFAULT_LABEL_COLORS: dict[str, str] = {
    "bug": "d73a4a",
    "feature-request": "a2eeef",
    "severity:low": "0e8a16",
    "severity:medium": "fbca04",
    "severity:high": "e99695",
    "severity:critical": "b60205",
    "env:dev": "c5def5",
    "env:production": "0052cc",
}

#: Default mapping from Bug-Fab status to GitHub issue state. ``fixed`` and
#: ``closed`` close the issue; everything else (``open`` /
#: ``investigating``) reopens it.
DEFAULT_STATE_MAP: dict[str, str] = {
    "open": "open",
    "investigating": "open",
    "fixed": "closed",
    "closed": "closed",
}

#: Pinned GitHub REST API version. Uses the documented header so the API
#: behaves deterministically across server-side rollouts.
GITHUB_API_VERSION = "2022-11-28"


def _truncate(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` chars, appending an ellipsis when cut."""
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


class GitHubSync:
    """Async client for the GitHub Issues sync feature.

    Parameters
    ----------
    pat:
        GitHub Personal Access Token (or fine-grained token) with
        ``issues:write`` scope on the target repo.
    repo:
        ``owner/name`` of the destination repository.
    api_base:
        Override for ``https://api.github.com`` (e.g., GitHub Enterprise
        Server installations).
    label_colors:
        Mapping of label name to hex color (no leading ``#``). Merged on
        top of :data:`DEFAULT_LABEL_COLORS` so consumers can add or
        override entries without re-stating the full set.
    state_map:
        Mapping of Bug-Fab status string to GitHub issue state
        (``open`` / ``closed``). Defaults to :data:`DEFAULT_STATE_MAP`.
    timeout:
        Per-request timeout in seconds passed to the underlying
        :class:`httpx.AsyncClient`.
    """

    def __init__(
        self,
        pat: str,
        repo: str,
        *,
        api_base: str = "https://api.github.com",
        label_colors: Mapping[str, str] | None = None,
        state_map: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._pat = pat
        self._repo = repo
        self._api_base = api_base.rstrip("/")
        self._label_colors: dict[str, str] = {
            **DEFAULT_LABEL_COLORS,
            **(dict(label_colors) if label_colors else {}),
        }
        self._state_map: dict[str, str] = {
            **DEFAULT_STATE_MAP,
            **(dict(state_map) if state_map else {}),
        }
        self._timeout = timeout
        self._labels_ensured = False

    @property
    def headers(self) -> dict[str, str]:
        """HTTP headers used for every outbound GitHub request."""
        return {
            "Authorization": f"Bearer {self._pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    async def ensure_labels(self) -> None:
        """Idempotently create every configured label on the target repo.

        Runs at most once per :class:`GitHubSync` instance — a successful
        first call sets ``self._labels_ensured`` and subsequent calls are
        no-ops. A 422 from GitHub means the label already exists and is
        treated as success.
        """
        if self._labels_ensured:
            return
        url = f"{self._api_base}/repos/{self._repo}/labels"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                for name, color in self._label_colors.items():
                    try:
                        resp = await client.post(
                            url,
                            json={"name": name, "color": color},
                            headers=self.headers,
                        )
                        if resp.status_code not in (200, 201, 422):
                            logger.warning(
                                "github_label_create_failed",
                                extra={
                                    "label": name,
                                    "status_code": resp.status_code,
                                    "body": _truncate(resp.text, 200),
                                },
                            )
                    except httpx.HTTPError as exc:
                        logger.warning(
                            "github_label_create_error",
                            extra={"label": name, "error": str(exc)},
                        )
        except Exception:  # pragma: no cover - defensive
            logger.exception("github_ensure_labels_unexpected_error")
            return
        self._labels_ensured = True

    async def create_issue(self, report: Mapping[str, Any]) -> tuple[int | None, str | None]:
        """Create a GitHub issue for a freshly persisted report.

        Returns ``(issue_number, issue_url)`` on success or ``(None, None)``
        on any failure (the call always returns; exceptions are logged,
        never raised).
        """
        await self.ensure_labels()
        title = _build_issue_title(report)
        body = _build_issue_body(report)
        labels = _build_issue_labels(report)
        url = f"{self._api_base}/repos/{self._repo}/issues"
        payload = {"title": title, "body": body, "labels": labels}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=self.headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "github_issue_create_error",
                extra={"report_id": report.get("id"), "error": str(exc)},
            )
            return None, None
        if resp.status_code not in (200, 201):
            logger.warning(
                "github_issue_create_failed",
                extra={
                    "report_id": report.get("id"),
                    "status_code": resp.status_code,
                    "body": _truncate(resp.text, 200),
                },
            )
            return None, None
        try:
            data = resp.json()
            return int(data["number"]), str(data["html_url"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "github_issue_response_malformed",
                extra={"report_id": report.get("id"), "error": str(exc)},
            )
            return None, None

    async def sync_issue_state(self, issue_number: int, status: str) -> bool:
        """PATCH an existing issue to match a Bug-Fab status change.

        Returns True on success, False on any failure. Unknown status
        values fall back to ``open`` (defensive — ``status`` should already
        have passed Pydantic validation upstream).
        """
        target_state = self._state_map.get(status, "open")
        url = f"{self._api_base}/repos/{self._repo}/issues/{issue_number}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.patch(url, json={"state": target_state}, headers=self.headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "github_issue_sync_error",
                extra={"issue_number": issue_number, "error": str(exc)},
            )
            return False
        if resp.status_code not in (200, 201):
            logger.warning(
                "github_issue_sync_failed",
                extra={
                    "issue_number": issue_number,
                    "status_code": resp.status_code,
                    "body": _truncate(resp.text, 200),
                },
            )
            return False
        return True


def _build_issue_title(report: Mapping[str, Any]) -> str:
    """Compose the GitHub issue title from a stored report."""
    report_type = str(report.get("report_type", "bug")).lower()
    prefix = "[Feature Request]" if report_type == "feature_request" else "[Bug]"
    title = str(report.get("title", "Untitled"))
    return f"{prefix} {title}"


def _build_issue_labels(report: Mapping[str, Any]) -> list[str]:
    """Compose the label list for a freshly created issue."""
    report_type = str(report.get("report_type", "bug")).lower()
    severity = str(report.get("severity", "medium")).lower()
    environment = str(report.get("environment", "")).lower()
    type_label = "feature-request" if report_type == "feature_request" else "bug"
    labels: list[str] = [type_label, f"severity:{severity}"]
    if environment:
        labels.append(f"env:{environment}")
    return labels


def _build_issue_body(report: Mapping[str, Any]) -> str:
    """Render the markdown body for a new GitHub issue.

    The body is intentionally compact at the top (description + expected
    behavior + a small key/value table) and pushes the verbose
    auto-captured context into a collapsible ``<details>`` block so the
    issue is readable without scrolling past 100 console-error stack
    traces.
    """
    description = str(report.get("description", "")).strip() or "_No description provided._"
    expected = str(report.get("expected_behavior", "")).strip()
    context = report.get("context") or {}
    if not isinstance(context, dict):
        context = {}

    rows: list[str] = []
    for label, key in (
        ("ID", "id"),
        ("Severity", "severity"),
        ("Module", "module"),
        ("Environment", "environment"),
        ("Reported At", "created_at"),
    ):
        value = report.get(key)
        if value:
            rows.append(f"| {label} | `{value}` |")
    page_url = context.get("url")
    if page_url:
        rows.append(f"| Page URL | `{page_url}` |")

    table = ""
    if rows:
        table = "| Field | Value |\n| --- | --- |\n" + "\n".join(rows)

    sections = ["## Description", description]
    if expected:
        sections += ["", "## Expected Behavior", expected]
    if table:
        sections += ["", "## Metadata", table]

    auto_block = _format_auto_context_block(context)
    if auto_block:
        sections += ["", auto_block]

    return "\n".join(sections)


def _format_auto_context_block(context: Mapping[str, Any]) -> str:
    """Render the collapsible auto-captured context section."""
    user_agent = context.get("user_agent") or ""
    viewport = ""
    width = context.get("viewport_width") or 0
    height = context.get("viewport_height") or 0
    if width and height:
        viewport = f"{width}x{height}"

    console_errors = context.get("console_errors") or []
    network_log = context.get("network_log") or []
    source_mapping = context.get("source_mapping") or {}

    if not (user_agent or viewport or console_errors or network_log or source_mapping):
        return ""

    parts: list[str] = ["<details><summary>Auto-captured context</summary>", ""]
    if user_agent:
        parts.append(f"- **User-Agent:** `{_truncate(str(user_agent), 200)}`")
    if viewport:
        parts.append(f"- **Viewport:** {viewport}")

    if console_errors:
        parts += ["", "**Console errors (first 10):**", "", "```"]
        for entry in list(console_errors)[:10]:
            level = str(entry.get("level", "log")) if isinstance(entry, dict) else "log"
            message = str(entry.get("message", "")) if isinstance(entry, dict) else str(entry)
            parts.append(f"[{level}] {_truncate(message, 200)}")
        parts.append("```")

    if network_log:
        parts += ["", "**Failed network requests (first 10):**", "", "```"]
        for entry in list(network_log)[:10]:
            if not isinstance(entry, dict):
                parts.append(_truncate(str(entry), 200))
                continue
            method = entry.get("method", "?")
            url = entry.get("url", "")
            status = entry.get("status", "?")
            parts.append(f"{method} {url} -> {status}")
        parts.append("```")

    if source_mapping:
        parts += ["", "**Source mapping:**", "", "```"]
        for key, value in source_mapping.items():
            parts.append(f"{key}: {value}")
        parts.append("```")

    parts += ["", "</details>"]
    return "\n".join(parts)
