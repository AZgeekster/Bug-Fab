"""Viewer router: HTML pages plus JSON management endpoints.

This module exposes :data:`viewer_router`, a FastAPI ``APIRouter`` that
serves both the HTML viewer (list + detail pages) and the JSON management
APIs (filterable list, single-report fetch, screenshot serve, status
update, delete, bulk operations).

Per-route gating
----------------
Three viewer permissions live on :class:`Settings`:

* ``can_edit_status`` — gates ``PUT /reports/{id}/status``.
* ``can_delete`` — gates ``DELETE /reports/{id}``.
* ``can_bulk`` — gates ``POST /bulk-close-fixed`` and
  ``POST /bulk-archive-closed``.

Each gated route depends on a small factory dependency that raises 403
when the corresponding flag is false. Mount-point auth still wraps these
endpoints — the permissions config is an additional in-band veto so
consumers who mount the viewer for read-only roles can disable
destructive actions without changing their auth middleware.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from bug_fab.config import Settings
from bug_fab.integrations.github import GitHubSync
from bug_fab.routers.submit import (
    get_github_sync,
    get_settings,
    get_storage,
)
from bug_fab.schemas import BugReportDetail, BugReportListResponse, BugReportStatusUpdate
from bug_fab.storage.base import Storage

logger = logging.getLogger(__name__)

viewer_router = APIRouter(tags=["bug-fab-viewer"])

#: Path-traversal guard — the file backend uses ``bug-NNN`` IDs and the
#: SQL backends use the same shape (with optional ``P`` / ``D`` env
#: prefix). Any input outside this character class is rejected with a
#: 404 before it reaches the storage layer.
_REPORT_ID_RE = re.compile(r"^bug-[A-Za-z]?\d{1,12}$")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _validate_report_id(report_id: str) -> str:
    """Reject IDs that fail the ``bug-NNN`` shape guard with a 404."""
    if not _REPORT_ID_RE.match(report_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found")
    return report_id


def _permission_dep(flag: str) -> Callable[[Settings], None]:
    """Build a FastAPI dependency that gates a route on a permission flag.

    Returns a coroutine-friendly callable that raises 403 when the named
    flag is false in :class:`Settings.viewer_permissions`. The flag name
    is captured by closure so each route gets its own dedicated dependency.
    """

    def _dep(settings: Settings = Depends(get_settings)) -> None:
        if not settings.viewer_permissions.get(flag, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"viewer action '{flag}' is disabled by configuration",
            )

    _dep.__name__ = f"require_{flag}"
    _dep.__doc__ = f"Reject the request when ``viewer_permissions[{flag!r}]`` is false."
    return _dep


require_can_edit_status = _permission_dep("can_edit_status")
require_can_delete = _permission_dep("can_delete")
require_can_bulk = _permission_dep("can_bulk")


def _resolve_csp_nonce(request: Request, settings: Settings) -> str | None:
    """Invoke the configured nonce provider, swallowing failures.

    A misbehaving provider (raises, returns a non-string) must not crash
    a viewer page render — CSP integration is opt-in glue and the safe
    fallback is to render without a nonce attribute. The browser will
    then refuse the script under strict CSP, which is the same outcome
    as a missing nonce — visible enough for the consumer to notice
    without breaking the whole page.
    """
    provider = settings.csp_nonce_provider
    if provider is None:
        return None
    try:
        nonce = provider(request)
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_csp_nonce_provider_failed")
        return None
    if nonce is None:
        return None
    return str(nonce)


def _viewer_actor(request: Request) -> str:
    """Extract a best-effort actor identifier for the lifecycle log.

    Bug-Fab v0.1 has no ``AuthAdapter``, so this is intentionally
    permissive — consumers who attach a user identifier to ``request.state``
    (for example via their own middleware) get it surfaced as the
    ``by`` field on lifecycle entries; everyone else sees an opaque
    placeholder.
    """
    actor = getattr(request.state, "bug_fab_actor", None)
    return str(actor) if actor else "viewer"


@viewer_router.get(
    "",
    response_class=HTMLResponse,
    summary="HTML list view of bug reports",
)
async def list_reports_html(
    request: Request,
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    module: str | None = None,
    environment: str | None = None,
) -> HTMLResponse:
    """Render the viewer's HTML list page with stat cards and filters."""
    effective_page_size = page_size or settings.viewer_page_size
    filters = _build_filters(
        status=status_filter, severity=severity, module=module, environment=environment
    )
    items, total = await storage.list_reports(filters, page, effective_page_size)
    stats = await _compute_stats(storage)
    total_pages = max((total + effective_page_size - 1) // effective_page_size, 1)
    context = {
        "items": items,
        "total": total,
        "stats": stats,
        "page": page,
        "page_size": effective_page_size,
        "total_pages": total_pages,
        "filters": {
            "status": status_filter or "",
            "severity": severity or "",
            "module": module or "",
            "environment": environment or "",
        },
        "permissions": settings.viewer_permissions,
        "csp_nonce": _resolve_csp_nonce(request, settings),
    }
    return templates.TemplateResponse(request, "list.html", context)


@viewer_router.get(
    "/reports",
    response_model=BugReportListResponse,
    summary="JSON list of bug reports",
)
async def list_reports_json(
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    module: str | None = None,
    environment: str | None = None,
) -> BugReportListResponse:
    """Return a paginated JSON list of bug-report summaries."""
    effective_page_size = page_size or settings.viewer_page_size
    filters = _build_filters(
        status=status_filter, severity=severity, module=module, environment=environment
    )
    items, total = await storage.list_reports(filters, page, effective_page_size)
    stats = await _compute_stats(storage)
    # Match the Flask adapter's wire shape — drop the "total" rollup key
    # (the envelope's top-level ``total`` is already authoritative) and
    # always emit the four lifecycle states, even when zero, so consumers
    # can rely on a stable stat-card shape.
    return BugReportListResponse(
        items=items,
        total=total,
        page=page,
        page_size=effective_page_size,
        stats={k: stats.get(k, 0) for k in ("open", "investigating", "fixed", "closed")},
    )


@viewer_router.get(
    "/reports/{report_id}",
    response_model=BugReportDetail,
    summary="JSON detail for a single bug report",
)
async def get_report_json(
    report_id: str,
    storage: Storage = Depends(get_storage),
) -> BugReportDetail:
    """Return the full JSON detail payload for a single report."""
    _validate_report_id(report_id)
    report = await storage.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found")
    return report


@viewer_router.get(
    "/{report_id}",
    response_class=HTMLResponse,
    summary="HTML detail view for a single bug report",
)
async def get_report_html(
    request: Request,
    report_id: str,
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render the HTML detail page (screenshot + metadata + lifecycle)."""
    _validate_report_id(report_id)
    report = await storage.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found")
    context = {
        "report": report,
        "permissions": settings.viewer_permissions,
        "csp_nonce": _resolve_csp_nonce(request, settings),
    }
    return templates.TemplateResponse(request, "detail.html", context)


@viewer_router.get(
    "/reports/{report_id}/screenshot",
    summary="Raw screenshot bytes",
)
async def get_screenshot(
    report_id: str,
    storage: Storage = Depends(get_storage),
) -> Response:
    """Return the report's screenshot image as raw bytes.

    The media type is always ``image/png``. PROTOCOL.md v0.1 locks the
    screenshot wire format to PNG, the intake router rejects anything
    else with 415, and ``html2canvas`` (the bundled client) only emits
    PNG. Existing reports persisted before this restriction tightened
    are served as ``image/png`` for back-compat — the magic-byte sniff
    on intake means stored bytes are always PNG.
    """
    _validate_report_id(report_id)
    path = await storage.get_screenshot_path(report_id)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png")


@viewer_router.put(
    "/reports/{report_id}/status",
    response_model=BugReportDetail,
    dependencies=[Depends(require_can_edit_status)],
    summary="Update a bug report's lifecycle status",
)
async def update_report_status(
    report_id: str,
    payload: BugReportStatusUpdate,
    request: Request,
    storage: Storage = Depends(get_storage),
    github_sync: GitHubSync | None = Depends(get_github_sync),
) -> BugReportDetail:
    """Apply a status change, append a lifecycle entry, sync to GitHub."""
    _validate_report_id(report_id)
    actor = _viewer_actor(request)
    try:
        updated = await storage.update_status(
            report_id,
            status=payload.status.value,
            fix_commit=payload.fix_commit,
            fix_description=payload.fix_description,
            by=actor,
        )
    except ValueError as exc:
        # Pydantic catches enum violations on the input model; ValueError
        # here means the storage layer rejected the transition for some
        # other reason (e.g., unknown id race). Surface as 422 to keep
        # the contract aligned with severity-style enum errors.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found")

    if github_sync is not None and updated.github_issue_number:
        try:
            await github_sync.sync_issue_state(updated.github_issue_number, payload.status.value)
        except Exception:  # pragma: no cover - defensive
            logger.exception("bug_fab_github_state_sync_failed", extra={"report_id": report_id})

    return updated


@viewer_router.delete(
    "/reports/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_can_delete)],
    summary="Permanently delete a bug report",
)
async def delete_report(
    report_id: str,
    storage: Storage = Depends(get_storage),
) -> Response:
    """Hard-delete the metadata + screenshot for a single report."""
    _validate_report_id(report_id)
    deleted = await storage.delete_report(report_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@viewer_router.post(
    "/bulk-close-fixed",
    dependencies=[Depends(require_can_bulk)],
    summary="Close every report currently in 'fixed' state",
)
async def bulk_close_fixed(
    request: Request,
    storage: Storage = Depends(get_storage),
) -> JSONResponse:
    """Transition every ``fixed`` report to ``closed``."""
    actor = _viewer_actor(request)
    closed = await storage.bulk_close_fixed(by=actor)
    return JSONResponse({"closed": closed})


@viewer_router.post(
    "/bulk-archive-closed",
    dependencies=[Depends(require_can_bulk)],
    summary="Archive every report currently in 'closed' state",
)
async def bulk_archive_closed(
    storage: Storage = Depends(get_storage),
) -> JSONResponse:
    """Move every ``closed`` report into the storage backend's archive area."""
    archived = await storage.bulk_archive_closed()
    return JSONResponse({"archived": archived})


def _build_filters(
    *,
    status: str | None,
    severity: str | None,
    module: str | None,
    environment: str | None,
) -> dict[str, str]:
    """Strip empty / whitespace-only filter values into a clean dict."""
    raw = {
        "status": status,
        "severity": severity,
        "module": module,
        "environment": environment,
    }
    return {key: value.strip() for key, value in raw.items() if value and value.strip()}


async def _compute_stats(storage: Storage) -> dict[str, int]:
    """Aggregate stat-card counts from the storage backend.

    Walks the four lifecycle states with one ``list_reports`` call each
    plus a final ``total`` query. Backends with cheap COUNT support can
    short-circuit by overriding this helper via a subclass; the default
    implementation is correct for the file backend's small-N workload.
    """
    stats: dict[str, int] = {}
    for state in ("open", "investigating", "fixed", "closed"):
        _, total = await storage.list_reports({"status": state}, page=1, page_size=1)
        stats[state] = total
    _, total = await storage.list_reports({}, page=1, page_size=1)
    stats["total"] = total
    return stats


# ----------------------------------------------------------------------
# Validation helpers re-exported for tests / adapter authors.
# ----------------------------------------------------------------------

__all__ = [
    "viewer_router",
    "require_can_edit_status",
    "require_can_delete",
    "require_can_bulk",
]


def _ensure_status_payload(raw: dict) -> BugReportStatusUpdate:
    """Validate a status-update body outside the FastAPI request flow.

    Exposed for adapter-author tests that want to exercise the same
    422-on-invalid-status guarantee without spinning up a TestClient.
    """
    try:
        return BugReportStatusUpdate.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(exc.errors()) from exc
