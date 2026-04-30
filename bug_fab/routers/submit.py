"""POST intake router for the Bug-Fab v0.1 wire protocol.

This module exposes :data:`submit_router`, a FastAPI ``APIRouter`` with a
single endpoint (``POST /bug-reports``) that:

1. Validates the multipart payload (``metadata`` JSON string +
   ``screenshot`` file).
2. Enforces per-IP rate limits when the consumer enables them.
3. Caps screenshot size and verifies PNG / JPEG magic bytes.
4. Captures the request-header ``User-Agent`` as the source-of-truth
   ``server_user_agent`` field, preserving any client-supplied value as
   ``client_reported_user_agent`` for diagnostics.
5. Persists the report through the configured :class:`Storage` backend.
6. Best-effort syncs to GitHub Issues when enabled.

Storage and configuration are resolved through dependency-injection
helpers (:func:`get_storage`, :func:`get_settings`, :func:`get_github_sync`)
so consumers can override any of them with FastAPI's ``app.dependency_overrides``
mechanism for tests or alternative wiring.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import ValidationError

from bug_fab._rate_limit import RateLimiter
from bug_fab.config import Settings
from bug_fab.integrations.github import GitHubSync
from bug_fab.schemas import BugReportCreate, BugReportIntakeResponse
from bug_fab.storage.base import Storage

logger = logging.getLogger(__name__)

submit_router = APIRouter(tags=["bug-fab"])

#: Magic-byte signatures for the two image formats accepted on intake.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"

#: Module-level singleton that callers replace via FastAPI dependency
#: overrides. The router never instantiates a default storage backend
#: because the choice (file / SQLite / Postgres / contrib) is the
#: consumer's call.
_STORAGE: Storage | None = None
_SETTINGS: Settings | None = None
_GITHUB_SYNC: GitHubSync | None = None
_RATE_LIMITER: RateLimiter | None = None


def configure(
    *,
    storage: Storage,
    settings: Settings | None = None,
    github_sync: GitHubSync | None = None,
) -> None:
    """Wire the router with a storage backend and (optionally) overrides.

    Consumers call this once during application startup. The ``settings``
    argument defaults to ``Settings.from_env()``; ``github_sync`` defaults
    to ``None`` (sync disabled) and is built automatically from settings
    when ``settings.github_enabled`` is true.
    """
    global _STORAGE, _SETTINGS, _GITHUB_SYNC, _RATE_LIMITER
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
    _RATE_LIMITER = RateLimiter(
        max_per_window=_SETTINGS.rate_limit_max,
        window_seconds=_SETTINGS.rate_limit_window_seconds,
    )


def get_storage() -> Storage:
    """Dependency: return the configured storage backend.

    Raises a 500 if :func:`configure` has not been called — the consumer
    forgot to wire the router during startup.
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


def get_rate_limiter() -> RateLimiter | None:
    """Dependency: return the per-IP limiter (or None when disabled)."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return None
    return _RATE_LIMITER


def _client_ip(request: Request) -> str:
    """Best-effort source-IP extraction.

    Honors ``X-Forwarded-For`` (first hop) when present so deployments
    behind a reverse proxy still meter per-end-user. Falls back to the
    direct peer address. Returns ``"unknown"`` when nothing is available
    so the limiter still sees a stable key.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _detect_image_kind(payload: bytes) -> str | None:
    """Return ``"png"`` / ``"jpeg"`` for known signatures, else ``None``."""
    if payload.startswith(_PNG_SIGNATURE):
        return "png"
    if payload.startswith(_JPEG_SIGNATURE):
        return "jpeg"
    return None


@submit_router.post(
    "/bug-reports",
    response_model=BugReportIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a bug report",
)
async def submit_bug_report(
    request: Request,
    metadata: str = Form(..., description="JSON-encoded BugReportCreate payload"),
    screenshot: UploadFile = File(..., description="Captured PNG or JPEG image"),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    github_sync: GitHubSync | None = Depends(get_github_sync),
    limiter: RateLimiter | None = Depends(get_rate_limiter),
) -> BugReportIntakeResponse:
    """Persist a new bug report and return its full detail payload."""
    if limiter is not None and not limiter.check(_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: max {settings.rate_limit_max} reports "
                f"per {settings.rate_limit_window_seconds} seconds"
            ),
        )

    # Parse and validate the metadata JSON. Pydantic validation errors
    # bubble up as 422 with the standard FastAPI error envelope; bad JSON
    # is its own 400 so consumers can distinguish "not parseable" from
    # "parseable but invalid."
    try:
        metadata_obj: dict[str, Any] = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"metadata is not valid JSON: {exc.msg}",
        ) from exc
    try:
        payload = BugReportCreate.model_validate(metadata_obj)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    # Buffer the upload into memory so we can validate magic bytes and
    # enforce the size cap before handing bytes to storage.
    screenshot_bytes = await screenshot.read()
    if not screenshot_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Screenshot file is empty",
        )
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(screenshot_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Screenshot exceeds maximum size of {settings.max_upload_mb} MiB",
        )
    if _detect_image_kind(screenshot_bytes) is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Screenshot must be a PNG or JPEG image",
        )

    # Build the persistence payload. The server is authoritative for
    # User-Agent, environment, and the protocol-version tag — the client
    # value (if any) is preserved separately for diagnostics.
    server_user_agent = request.headers.get("user-agent", "")
    client_user_agent = payload.context.user_agent
    environment = payload.context.environment or metadata_obj.get("environment") or ""
    metadata_dict = payload.model_dump(mode="json")
    metadata_dict["server_user_agent"] = server_user_agent
    metadata_dict["client_reported_user_agent"] = client_user_agent
    metadata_dict["environment"] = environment

    try:
        report_id = await storage.save_report(metadata_dict, screenshot_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("bug_fab_storage_save_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist bug report",
        ) from exc

    detail = await storage.get_report(report_id)
    if detail is None:  # pragma: no cover - storage contract violation
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored report could not be read back",
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
                # Persist the link to storage if the backend supports it. If not,
                # consumers will see it appear on the next GET /reports/{id}.
                update_hook = getattr(storage, "set_github_link", None)
                if callable(update_hook):
                    await update_hook(report_id, issue_number, issue_url)
        except Exception:  # pragma: no cover - defensive
            logger.exception("bug_fab_github_sync_failed", extra={"report_id": report_id})

    # Per PROTOCOL.md § Response — minimal envelope, NOT the full BugReportDetail.
    # `stored_at` is an opaque diagnostic string; consumers wanting the full
    # stored shape do GET /reports/{id} after.
    return BugReportIntakeResponse(
        id=report_id,
        received_at=detail.created_at,
        stored_at=f"bug-fab://reports/{report_id}",
        github_issue_url=github_issue_url,
    )
