"""POST intake router for the Bug-Fab v0.1 wire protocol.

This module exposes :data:`submit_router`, a FastAPI ``APIRouter`` with a
single endpoint (``POST /bug-reports``) that:

1. Enforces per-IP rate limits when the consumer enables them.
2. Validates the multipart payload (``metadata`` JSON string +
   ``screenshot`` file) through the shared
   :func:`bug_fab.intake.validate_payload` pipeline — the same call the
   Flask and Django adapters make, so all three accept and reject
   identical requests: size caps, then content-type + PNG magic bytes
   (v0.1 locks the screenshot media type to ``image/png``), then JSON
   parse + protocol-version gate + schema validation.
3. Captures the request-header ``User-Agent`` as the source-of-truth
   ``server_user_agent`` field, preserving any client-supplied value as
   ``client_reported_user_agent`` for diagnostics.
4. Persists the report through the configured :class:`Storage` backend.
5. Best-effort syncs to GitHub Issues when enabled.

Storage and configuration are resolved through dependency-injection
helpers (:func:`get_storage`, :func:`get_settings`, :func:`get_github_sync`)
so consumers can override any of them with FastAPI's ``app.dependency_overrides``
mechanism for tests or alternative wiring.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Container
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from bug_fab._observability import EVENT_REPORT_RECEIVED
from bug_fab._observability import emit as emit_event
from bug_fab._rate_limit import RateLimiter, resolve_client_ip
from bug_fab._redact import redact_report
from bug_fab.config import Settings
from bug_fab.intake import (
    IntakeError,
    PayloadTooLarge,
    UnsupportedMediaType,
    UnsupportedProtocolVersion,
    max_request_bytes,
    validate_payload,
)
from bug_fab.intake import (
    ValidationError as IntakeValidationError,
)
from bug_fab.integrations.github import GitHubSync
from bug_fab.integrations.webhook import WebhookSync
from bug_fab.routers._errors import protocol_error
from bug_fab.schemas import BugReportIntakeResponse
from bug_fab.storage.base import Storage

logger = logging.getLogger(__name__)


class _ContentLengthLimitedRoute(APIRoute):
    """Reject an oversized request by its declared ``Content-Length``.

    FastAPI parses the multipart body *before* the path operation (and
    before any dependency) runs, so an in-handler size check cannot stop
    the body from being buffered. This custom route wraps the handler and
    inspects ``Content-Length`` first, returning the protocol ``413``
    envelope without ever reading the body. A missing header (chunked
    transfer) or a non-integer value falls through to the normal handler,
    where the precise per-field caps still apply. See
    :func:`bug_fab.intake.max_request_bytes`.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def limited(request: Request) -> Response:
            raw = request.headers.get("content-length")
            if raw is not None:
                try:
                    declared = int(raw)
                except ValueError:
                    declared = -1
                if declared >= 0:
                    settings = get_settings()
                    limit = max_request_bytes(
                        settings.max_upload_mb * 1024 * 1024,
                        settings.max_metadata_kb * 1024,
                    )
                    if declared > limit:
                        return protocol_error(
                            status.HTTP_413_CONTENT_TOO_LARGE,
                            "payload_too_large",
                            f"Request body exceeds maximum size of {limit} bytes",
                            limit_bytes=limit,
                        )
            return await original(request)

        return limited


submit_router = APIRouter(tags=["bug-fab"], route_class=_ContentLengthLimitedRoute)

#: Module-level singleton that callers replace via FastAPI dependency
#: overrides. The router never instantiates a default storage backend
#: because the choice (file / SQLite / Postgres / contrib) is the
#: consumer's call.
_STORAGE: Storage | None = None
_SETTINGS: Settings | None = None
_GITHUB_SYNC: GitHubSync | None = None
_WEBHOOK_SYNC: WebhookSync | None = None
_RATE_LIMITER: RateLimiter | None = None


def configure(
    *,
    storage: Storage,
    settings: Settings | None = None,
    github_sync: GitHubSync | None = None,
    webhook_sync: WebhookSync | None = None,
) -> None:
    """Wire the router with a storage backend and (optionally) overrides.

    Consumers call this once during application startup. The ``settings``
    argument defaults to ``Settings.from_env()``; ``github_sync`` defaults
    to ``None`` (sync disabled) and is built automatically from settings
    when ``settings.github_enabled`` is true. ``webhook_sync`` follows the
    same shape — explicit instance wins, otherwise built from
    ``settings.webhook_enabled`` + ``settings.webhook_url``.
    """
    global _STORAGE, _SETTINGS, _GITHUB_SYNC, _WEBHOOK_SYNC, _RATE_LIMITER
    _STORAGE = storage
    _SETTINGS = settings or Settings.from_env()
    if (
        github_sync is None
        and _SETTINGS.github_enabled
        and _SETTINGS.github_pat
        and _SETTINGS.github_repo
    ):
        github_sync = GitHubSync(
            pat=_SETTINGS.github_pat,
            repo=_SETTINGS.github_repo,
            api_base=_SETTINGS.github_api_base,
        )
    _GITHUB_SYNC = github_sync
    if webhook_sync is None and _SETTINGS.webhook_enabled and _SETTINGS.webhook_url:
        webhook_sync = WebhookSync(
            _SETTINGS.webhook_url,
            headers=_SETTINGS.webhook_headers,
            timeout_seconds=_SETTINGS.webhook_timeout_seconds,
            max_attempts=_SETTINGS.webhook_max_attempts,
            retry_backoff_seconds=_SETTINGS.webhook_retry_backoff_seconds,
            dlq_dir=_SETTINGS.webhook_dlq_dir or None,
        )
    _WEBHOOK_SYNC = webhook_sync
    _RATE_LIMITER = RateLimiter(
        max_per_window=_SETTINGS.rate_limit_max,
        window_seconds=_SETTINGS.rate_limit_window_seconds,
    )


def get_storage() -> Storage:
    """Dependency: return the configured storage backend.

    Raises a 500 if :func:`configure` has not been called — the consumer
    forgot to wire the router during startup.

    This is the one path that cannot emit the protocol's ``{"error",
    "detail"}`` envelope: FastAPI discards a dependency's return value, so
    a dependency can only short-circuit by raising, and a raised
    ``HTTPException`` always serializes to ``{"detail": ...}``. Documented
    as a known 5xx deviation in ``docs/PROTOCOL.md``.
    """
    if _STORAGE is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Bug-Fab storage backend is not configured",
        )
    return _STORAGE


def get_settings() -> Settings:
    """Dependency: return the active :class:`Settings` instance."""
    if _SETTINGS is None:
        # Falling back to env-derived defaults keeps the router usable even
        # when consumers forget the configure() call (storage is the only
        # hard requirement; settings have safe defaults).
        return Settings.from_env()
    return _SETTINGS


def get_github_sync() -> GitHubSync | None:
    """Dependency: return the GitHub sync client (or None when disabled)."""
    return _GITHUB_SYNC


def get_webhook_sync() -> WebhookSync | None:
    """Dependency: return the generic webhook client (or None when disabled)."""
    return _WEBHOOK_SYNC


def get_rate_limiter() -> RateLimiter | None:
    """Dependency: return the per-IP limiter (or None when disabled)."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return None
    return _RATE_LIMITER


def _client_ip(request: Request, trusted_proxies: Container[str]) -> str:
    """Best-effort source-IP extraction for the rate-limit key.

    Delegates the ``X-Forwarded-For`` trust decision to
    :func:`bug_fab._rate_limit.resolve_client_ip`: the header is honored
    only when the direct peer is a configured trusted proxy, so a spoofed
    header cannot mint a fresh bucket per request.
    """
    peer = request.client.host if request.client else None
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for"), trusted_proxies)


@submit_router.post(
    "/bug-reports",
    response_model=BugReportIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a bug report",
    responses={
        400: {"description": "Malformed metadata JSON or unknown protocol_version"},
        413: {"description": "Screenshot or metadata exceeds its configured size cap"},
        415: {"description": "Screenshot is not a PNG (by content-type or magic bytes)"},
        422: {"description": "Metadata parses as JSON but fails schema validation"},
        429: {"description": "Per-IP rate limit exceeded"},
        500: {"description": "Storage backend unavailable or write failed"},
    },
)
async def submit_bug_report(
    request: Request,
    metadata: str = Form(..., description="JSON-encoded BugReportCreate payload"),
    screenshot: UploadFile = File(..., description="Captured PNG image"),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    github_sync: GitHubSync | None = Depends(get_github_sync),
    webhook_sync: WebhookSync | None = Depends(get_webhook_sync),
    limiter: RateLimiter | None = Depends(get_rate_limiter),
) -> BugReportIntakeResponse | JSONResponse:
    """Persist a new bug report and return its full detail payload.

    Error paths ``return`` the protocol envelope rather than raising
    ``HTTPException`` — FastAPI bypasses ``response_model`` when a handler
    returns a ``Response``, and the response's own status wins over the
    decorator's ``status_code=201``. See :mod:`bug_fab.routers._errors`.
    """
    if limiter is not None and not limiter.check(
        _client_ip(request, settings.rate_limit_trusted_proxies)
    ):
        return protocol_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            (
                f"Rate limit exceeded: max {settings.rate_limit_max} reports "
                f"per {settings.rate_limit_window_seconds} seconds"
            ),
            retry_after_seconds=settings.rate_limit_window_seconds,
        )

    # Validate through the shared pipeline in :mod:`bug_fab.intake` — the
    # same call Flask and Django make, so all three Python adapters accept
    # and reject identical requests with identical error envelopes. The
    # exception→envelope mapping below mirrors the Flask blueprint's.
    # Buffer the upload first; the pre-parse Content-Length route guard has
    # already bounded the whole body, and the per-field caps re-check below.
    screenshot_bytes = await screenshot.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        validated = validate_payload(
            metadata_json=metadata,
            screenshot_bytes=screenshot_bytes,
            screenshot_content_type=(screenshot.content_type or "image/png"),
            request_user_agent=request.headers.get("user-agent"),
            max_screenshot_bytes=max_bytes,
            max_metadata_bytes=settings.max_metadata_kb * 1024,
        )
    except PayloadTooLarge as exc:
        return protocol_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "payload_too_large",
            exc.message,
            limit_bytes=exc.limit_bytes if exc.limit_bytes is not None else max_bytes,
        )
    except UnsupportedMediaType as exc:
        return protocol_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_media_type",
            exc.message,
        )
    except UnsupportedProtocolVersion as exc:
        return protocol_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except IntakeValidationError as exc:
        # Pydantic errors land in ``exc.detail``; JSON-decode failures
        # leave it empty and put the message on ``exc.message``.
        if exc.detail:
            return protocol_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "schema_error", exc.detail)
        return protocol_error(status.HTTP_400_BAD_REQUEST, "validation_error", exc.message)
    except IntakeError as exc:  # pragma: no cover - defensive catch-all
        return protocol_error(exc.status_code, exc.code, exc.message)

    # Build the persistence payload. The server is authoritative for
    # User-Agent, environment, and the protocol-version tag — the client
    # value (if any) is preserved separately for diagnostics.
    payload = validated.metadata
    server_user_agent = validated.user_agent
    client_user_agent = payload.context.user_agent
    try:
        metadata_obj: dict[str, Any] = json.loads(metadata)
    except json.JSONDecodeError:  # pragma: no cover - already validated
        metadata_obj = {}
    environment = payload.context.environment or metadata_obj.get("environment") or ""
    metadata_dict = payload.model_dump(mode="json")
    metadata_dict["server_user_agent"] = server_user_agent
    metadata_dict["client_reported_user_agent"] = client_user_agent
    metadata_dict["environment"] = environment

    # Opt-in PII redaction runs before persistence so masked values
    # are what land on disk — there's no second copy of the raw text
    # to leak later. See bug_fab._redact for the documented patterns.
    if settings.redact_pii:
        metadata_dict = redact_report(metadata_dict)

    try:
        report_id = await storage.save_report(metadata_dict, screenshot_bytes)
    except ValueError as exc:
        return protocol_error(status.HTTP_400_BAD_REQUEST, "validation_error", str(exc))
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "bug_fab_storage_save_failed",
            extra={"event": "storage_save_failed"},
        )
        return protocol_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Failed to persist bug report",
        )

    detail = await storage.get_report(report_id)
    if detail is None:  # pragma: no cover - storage contract violation
        return protocol_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Stored report could not be read back",
        )

    # Structured lifecycle event — consumers wiring a JSON log handler
    # (Loki / Datadog / Sentry / etc.) on the `bug_fab.events` logger
    # tree pick this up automatically. Stable field vocabulary —
    # adding fields is non-breaking; renaming or removing them is.
    emit_event(
        EVENT_REPORT_RECEIVED,
        report_id=report_id,
        severity=detail.severity,
        status=detail.status,
        report_type=detail.report_type,
        module=detail.module,
        environment=environment,
        has_screenshot=detail.has_screenshot,
        client_ip=_client_ip(request, settings.rate_limit_trusted_proxies),
    )

    # GitHub sync is best-effort. A failed POST does not roll back the
    # local save; the report simply lacks a github_issue_url until a
    # later replay or manual cross-link.
    github_issue_url: str | None = None
    if github_sync is not None:
        try:
            issue_number, issue_url = await github_sync.create_issue(detail.model_dump(mode="json"))
            if issue_number is not None and issue_url is not None:
                github_issue_url = issue_url
                await storage.set_github_link(report_id, issue_number, issue_url)
        except Exception:  # pragma: no cover - defensive
            logger.exception("bug_fab_github_sync_failed", extra={"report_id": report_id})

    # Generic webhook delivery — fires AFTER GitHub sync so the payload
    # includes ``github_issue_url`` when both integrations are enabled.
    # Same best-effort contract: a failed POST is logged at WARN and
    # never blocks the intake response. Designed for Slack incoming-
    # webhooks, Linear project webhooks, n8n triggers, etc.
    if webhook_sync is not None:
        try:
            payload = detail.model_dump(mode="json")
            if github_issue_url is not None:
                payload["github_issue_url"] = github_issue_url
            await webhook_sync.send(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("bug_fab_webhook_sync_failed", extra={"report_id": report_id})

    # Per PROTOCOL.md § Response — minimal envelope, NOT the full BugReportDetail.
    # `stored_at` is an opaque diagnostic string; consumers wanting the full
    # stored shape do GET /reports/{id} after.
    return BugReportIntakeResponse(
        id=report_id,
        received_at=detail.created_at,
        stored_at=f"bug-fab://reports/{report_id}",
        github_issue_url=github_issue_url,
    )
