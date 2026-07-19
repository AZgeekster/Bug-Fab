"""Shared delivery primitives for Bug-Fab's optional outbound integrations.

Every integration submodule (``slack``, ``discord``, ``teams``,
``pagerduty``, ``linear``, ``github``, and the generic ``webhook``)
transforms a ``BugReportDetail``-shaped payload and POSTs it to a
third-party service. The transform differs per service, but the
delivery mechanics — fire the request, swallow every exception, treat a
non-2xx as a failure, and log at WARN — were copy-pasted into each
module. This module owns that shared mechanics so the integrations keep
only their service-specific payload builders.

Two primitives live here:

* :func:`truncate` — the single canonical text cap. It replaced six
  copies that had drifted into two incompatible forms (an ASCII
  ``...`` at ``limit - 3`` and a Unicode ``…`` at ``limit - 1``); the
  Unicode form is canonical because it guarantees the result never
  exceeds ``limit`` characters.
* :func:`post_json` — the "POST one JSON body, classify the response,
  return a bool" delivery used by the fire-and-forget chat integrations
  (Slack, Discord, Teams) and PagerDuty. It never raises: a down or
  misconfigured receiver logs at WARN and returns ``False`` so intake
  always succeeds.

These are free functions rather than a base class on purpose: the
integration constructors genuinely diverge (Discord carries a username,
Linear an API key and team id, PagerDuty a routing key and escalation
set), so composition fits better than a shared ``__init__``. Linear and
GitHub need the raw response after delivery (GraphQL error arrays,
multi-request issue flows) and so drive :func:`truncate` only, keeping
their own request logic.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

#: Cap applied to a failed receiver's response body before it lands in a
#: WARN log line — keeps a chatty 500 page from flooding the log.
LOG_BODY_LIMIT = 200


def truncate(text: str, limit: int) -> str:
    """Hard-cap ``text`` at ``limit`` chars, appending an ellipsis on overflow.

    The ellipsis is the single Unicode character ``…`` placed at
    ``limit - 1`` so the returned string never exceeds ``limit``
    characters. Trailing whitespace before the cut is stripped so the
    ellipsis sits flush against the last word.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class DeliveryEvents:
    """The three WARN event names a service emits on a failed delivery.

    Kept as per-service string literals (rather than derived from a
    service slug) so each name stays greppable in its own module and the
    structured-log event contract is unchanged by the extraction.
    """

    error: str
    unexpected: str
    failed: str


async def post_json(
    *,
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
    log: logging.Logger,
    events: DeliveryEvents,
    report_id: Any = None,
    headers: Mapping[str, str] | None = None,
    log_url: str | None = None,
) -> bool:
    """POST ``payload`` as JSON and classify the response into a bool.

    Owns the delivery mechanics shared by the fire-and-forget
    integrations: the :class:`httpx.AsyncClient` call, exception
    handling, the ``status_code // 100 != 2`` check, response-body
    truncation, and WARN logging. Returns ``True`` on a 2xx response,
    ``False`` on any transport error, timeout, or non-2xx status. Never
    raises — a failing receiver must never block the intake path.

    ``log`` and ``events`` are supplied by the caller so each failure is
    logged under the caller's own module logger with its own event
    names, preserving the pre-extraction operational contract.
    ``log_url`` is added to every log record's ``extra`` when provided
    (already redacted by the caller where the URL carries a secret);
    omit it for receivers whose URL adds no diagnostic value.
    """
    send_headers = {"Content-Type": "application/json"}
    if headers:
        send_headers.update({str(k): str(v) for k, v in headers.items()})
    base_extra: dict[str, Any] = {"report_id": report_id}
    if log_url is not None:
        base_extra["url"] = log_url
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=dict(payload), headers=send_headers)
    except httpx.HTTPError as exc:
        log.warning(events.error, extra={**base_extra, "error": str(exc)})
        return False
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(events.unexpected, extra={**base_extra, "error": str(exc)})
        return False
    if resp.status_code // 100 != 2:
        log.warning(
            events.failed,
            extra={
                **base_extra,
                "status_code": resp.status_code,
                "body": truncate(resp.text, LOG_BODY_LIMIT),
            },
        )
        return False
    return True


__all__ = [
    "LOG_BODY_LIMIT",
    "DeliveryEvents",
    "post_json",
    "truncate",
]
