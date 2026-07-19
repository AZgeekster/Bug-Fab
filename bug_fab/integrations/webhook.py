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

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from bug_fab._redact import safe_url

logger = logging.getLogger(__name__)

#: Default per-request timeout in seconds. Webhook receivers (Slack,
#: Linear, n8n) typically respond well under one second; the cap keeps
#: a slow downstream from stretching the intake path. Configurable via
#: the ``timeout_seconds`` constructor argument or the
#: ``BUG_FAB_WEBHOOK_TIMEOUT_SECONDS`` env var.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Default total send attempts. ``1`` preserves the historical
#: fire-and-forget shape — no retry, fail-fast. Consumers that wire a
#: real receiver (Slack, Linear, n8n) usually want 2 or 3 to ride out
#: transient 5xx blips. Bounded so a chronically broken downstream
#: doesn't stretch intake.
DEFAULT_MAX_ATTEMPTS = 1

#: Base delay between retry attempts (seconds). Exponential backoff
#: doubles per attempt: 0.5s → 1s → 2s → 4s. Cap implied by
#: ``max_attempts`` × the doubling factor.
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5


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
    max_attempts:
        Total send attempts including the first. ``1`` (default)
        preserves historical fire-and-forget behavior. Values >1
        enable bounded exponential-backoff retry on TRANSIENT errors
        (5xx, timeout, transport failure); 4xx responses NEVER retry
        because the receiver said the request itself was wrong.
    retry_backoff_seconds:
        Base delay between attempts. Doubles each retry: 0.5 → 1 → 2.
        Only consulted when ``max_attempts > 1``.
    dlq_dir:
        Optional directory where terminal failures (all retries
        exhausted) get persisted as JSON envelopes for later replay.
        When unset, failures are logged only. The directory is created
        on first use; one file per terminal failure named
        ``<UTC timestamp>_<report_id>.json``.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        dlq_dir: Path | str | None = None,
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
        # Clamp to 1 — a negative or zero max_attempts would silently
        # skip the request entirely, which is never what a consumer
        # wants. Surface the bad config by clamping rather than raising.
        self._max_attempts = max(1, int(max_attempts))
        self._backoff = max(0.0, float(retry_backoff_seconds))
        self._dlq_dir: Path | None = Path(dlq_dir) if dlq_dir else None

    @property
    def url(self) -> str:
        """The destination URL (read-only outside of construction)."""
        return self._url

    @property
    def headers(self) -> dict[str, str]:
        """Headers used on every outbound webhook POST."""
        return dict(self._headers)

    @property
    def max_attempts(self) -> int:
        """Total attempts per send (1 = no retry)."""
        return self._max_attempts

    @property
    def dlq_dir(self) -> Path | None:
        """Dead-letter directory, or ``None`` when DLQ is disabled."""
        return self._dlq_dir

    async def send(self, report: Mapping[str, Any]) -> bool:
        """POST the report payload to the configured webhook URL.

        ``report`` is expected to be the JSON-mode dump of
        :class:`bug_fab.schemas.BugReportDetail` — the same shape the
        viewer's ``GET /reports/{id}`` endpoint emits. Any
        JSON-serializable mapping is accepted; the method does not
        re-validate the structure because the caller has already
        round-tripped it through Pydantic.

        On a transient failure (5xx, timeout, transport error) the
        method retries with exponential backoff up to ``max_attempts``.
        A 4xx is treated as a permanent receiver-side rejection and
        fails fast without retry. After all attempts are exhausted,
        the report is optionally persisted to ``dlq_dir`` so a later
        replay can re-drive it.

        Returns ``True`` on a 2xx response, ``False`` on terminal
        failure. The call always returns — exceptions are caught and
        logged so a failing webhook never blocks the intake response
        path.
        """
        last_error: str = ""
        for attempt in range(1, self._max_attempts + 1):
            outcome, detail = await self._attempt_once(report, attempt)
            if outcome == "ok":
                return True
            last_error = detail
            if outcome == "permanent":
                # 4xx — receiver said the request is wrong; retrying
                # won't help. Persist to DLQ and exit.
                break
            # Transient — back off and try again, unless we're out of
            # attempts. Exponential: 0.5, 1.0, 2.0, ...
            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))
        # All attempts exhausted (or permanent failure on first try):
        # write to DLQ if configured.
        self._persist_dead_letter(report, last_error)
        return False

    async def _attempt_once(self, report: Mapping[str, Any], attempt: int) -> tuple[str, str]:
        """Perform a single POST; return (outcome, detail) classification.

        outcome ∈ ``{"ok", "transient", "permanent"}``. ``detail`` is a
        human-readable last-error string for logging / DLQ payloads.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=dict(report), headers=self._headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "bug_fab_webhook_send_error",
                extra={
                    "report_id": report.get("id"),
                    "url": safe_url(self._url),
                    "attempt": attempt,
                    "max_attempts": self._max_attempts,
                    "error": str(exc),
                },
            )
            return "transient", f"httpx.HTTPError: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_webhook_send_unexpected_error",
                extra={
                    "report_id": report.get("id"),
                    "url": safe_url(self._url),
                    "attempt": attempt,
                    "error": str(exc),
                },
            )
            return "transient", f"unexpected: {exc}"
        if resp.status_code // 100 == 2:
            return "ok", ""
        body = resp.text
        if len(body) > 200:
            body = body[:197] + "..."
        logger.warning(
            "bug_fab_webhook_send_failed",
            extra={
                "report_id": report.get("id"),
                "url": safe_url(self._url),
                "attempt": attempt,
                "max_attempts": self._max_attempts,
                "status_code": resp.status_code,
                "body": body,
            },
        )
        # 4xx is permanent (bad request shape, auth, rate-limit-by-token);
        # 5xx is transient and worth a retry.
        outcome = "permanent" if 400 <= resp.status_code < 500 else "transient"
        return outcome, f"HTTP {resp.status_code}: {body}"

    def _persist_dead_letter(self, report: Mapping[str, Any], last_error: str) -> None:
        """Write a JSON envelope describing the terminal failure to the DLQ dir.

        Best-effort: a failure here logs and returns without raising —
        a broken DLQ disk should NOT crash intake.
        """
        if self._dlq_dir is None:
            return
        try:
            self._dlq_dir.mkdir(parents=True, exist_ok=True)
            report_id = str(report.get("id") or "unknown")
            now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            filename = f"{now}_{report_id}.json"
            envelope = {
                "persisted_at": datetime.now(timezone.utc).isoformat(),
                "source_url": self._url,
                "last_error": last_error,
                "report": dict(report),
            }
            tmp = self._dlq_dir / (filename + ".tmp")
            tmp.write_text(json.dumps(envelope, ensure_ascii=True), encoding="utf-8")
            tmp.replace(self._dlq_dir / filename)
            logger.info(
                "bug_fab_webhook_dead_letter_written",
                extra={"report_id": report_id, "dlq_path": str(self._dlq_dir / filename)},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "bug_fab_webhook_dead_letter_write_failed",
                extra={
                    "report_id": report.get("id"),
                    "dlq_dir": str(self._dlq_dir),
                    "error": str(exc),
                },
            )


async def replay_dead_letters(
    sync: WebhookSync,
    dlq_dir: Path | str | None = None,
    *,
    delete_on_success: bool = True,
) -> dict[str, int]:
    """Walk ``dlq_dir`` and re-send every envelope through ``sync``.

    Uses ``sync.dlq_dir`` when ``dlq_dir`` is None. Returns a stats
    dict ``{attempted, succeeded, failed, malformed}``. Successful
    re-sends are deleted from disk by default; pass
    ``delete_on_success=False`` to keep an audit trail.

    Designed to be invoked from an operator script (cron, manual SSH
    after a downstream outage) rather than from inside the intake
    request path.
    """
    directory = Path(dlq_dir) if dlq_dir is not None else sync.dlq_dir
    stats = {"attempted": 0, "succeeded": 0, "failed": 0, "malformed": 0}
    if directory is None or not directory.is_dir():
        return stats
    for path in sorted(directory.glob("*.json")):
        stats["attempted"] += 1
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            report = envelope.get("report")
            if not isinstance(report, Mapping):
                raise ValueError("envelope.report not a mapping")
        except Exception as exc:
            logger.warning(
                "bug_fab_webhook_dlq_malformed",
                extra={"path": str(path), "error": str(exc)},
            )
            stats["malformed"] += 1
            continue
        ok = await sync.send(report)
        if ok:
            stats["succeeded"] += 1
            if delete_on_success:
                try:
                    path.unlink()
                except OSError as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "bug_fab_webhook_dlq_unlink_failed",
                        extra={"path": str(path), "error": str(exc)},
                    )
        else:
            stats["failed"] += 1
    return stats


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "WebhookSync",
    "parse_headers_env",
    "replay_dead_letters",
]
