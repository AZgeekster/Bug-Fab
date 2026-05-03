"""Plain-Django views implementing the Bug-Fab v0.1 wire protocol.

No DRF — :class:`~django.http.JsonResponse` and function-based views
keep the dependency surface minimal and avoid the camelCase-renderer
trap that DRF's auto-renderers introduce on shared
:class:`~rest_framework.serializers.Serializer` projects.

Eight endpoints map one-to-one onto the FastAPI reference routers:

================================  =========================================
Path                              Function
================================  =========================================
``POST /bug-reports``             :func:`intake_view`
``GET  /``                        :func:`report_list_html`
``GET  /reports``                 :func:`report_list_json`
``GET  /reports/{id}``            :func:`report_detail_json`
``GET  /{id}``                    :func:`report_detail_html`
``GET  /reports/{id}/screenshot`` :func:`screenshot_view`
``PUT  /reports/{id}/status``     :func:`status_update_view`
``DELETE /reports/{id}``          :func:`delete_view`
``POST /bulk-close-fixed``        :func:`bulk_close_fixed_view`
``POST /bulk-archive-closed``     :func:`bulk_archive_closed_view`
================================  =========================================

Intake and viewer mutation routes are ``@csrf_exempt`` because
consumers integrate Bug-Fab over their own session boundary — the
frontend bundle posts JSON / multipart from whatever page the user is
on, without participating in Django's CSRF token flow. Mount-prefix
auth is the line of defense (per ``docs/PROTOCOL.md`` § Auth).
Consumers wanting tighter CSRF coverage can wrap the URL include with
``django.views.decorators.csrf.csrf_protect``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from pydantic import ValidationError as PydanticValidationError

from bug_fab.intake import (
    PayloadTooLarge,
    UnsupportedMediaType,
    validate_payload,
)
from bug_fab.intake import (
    ValidationError as IntakeValidationError,
)
from bug_fab.schemas import BugReportStatusUpdate

from .storage import REPORT_ID_RE, DjangoORMStorage, StorageError


def _resolve_bundle_path():
    """Locate the canonical ``bug-fab.js`` bundle on disk.

    Wheel installs place the bundle at ``<site-packages>/bug_fab/static/``
    via ``[tool.hatch.build.targets.wheel.force-include]``. Editable
    installs leave it one directory above the package at
    ``<repo>/static/``. Probe both layouts so the same view runs in either.
    """
    from importlib import resources
    from pathlib import Path

    package_root = Path(resources.files("bug_fab"))
    for candidate in (package_root / "static", package_root.parent / "static"):
        bundle = candidate / "bug-fab.js"
        if bundle.is_file():
            return bundle
    return None


logger = logging.getLogger(__name__)

#: Default screenshot cap mirrors the wire-protocol envelope (10 MiB).
#: Consumers can tighten this via the ``BUG_FAB_MAX_UPLOAD_BYTES`` env
#: var; loosening past Django's ``DATA_UPLOAD_MAX_MEMORY_SIZE`` setting
#: is silently ineffective — Django rejects the request body before our
#: view is invoked. Document the Django setting alongside the cap.
DEFAULT_MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024


def _max_upload_bytes() -> int:
    """Resolve the screenshot byte cap from env (or the default)."""
    raw = os.environ.get("BUG_FAB_MAX_UPLOAD_BYTES")
    if not raw:
        return DEFAULT_MAX_SCREENSHOT_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_SCREENSHOT_BYTES
    return max(value, 1)


def _err(code: str, detail: Any, http_status: int) -> JsonResponse:
    """Return the protocol-standard ``{"error", "detail"}`` envelope."""
    body: dict[str, Any] = {"error": code, "detail": detail}
    if code == "payload_too_large":
        body["limit_bytes"] = _max_upload_bytes()
    return JsonResponse(body, status=http_status)


def _get_storage() -> DjangoORMStorage:
    """Return a fresh storage handle.

    The Django ORM keeps connection management at the framework level,
    so creating a new :class:`DjangoORMStorage` per request is a no-op
    cost-wise. Tests and subclasses can monkeypatch this hook to swap
    implementations.
    """
    return DjangoORMStorage()


def _viewer_actor(request: HttpRequest) -> str:
    """Best-effort actor identifier for the lifecycle log.

    Mirrors the FastAPI viewer's ``_viewer_actor`` helper. Authenticated
    users surface as their username; consumers attaching a richer
    identifier to ``request.bug_fab_actor`` (via their own middleware)
    win out. Falls back to ``"viewer"`` so the log always has a value.
    """
    explicit = getattr(request, "bug_fab_actor", None)
    if explicit:
        return str(explicit)
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return getattr(user, "username", None) or "viewer"
    return "viewer"


def _build_filters(request: HttpRequest) -> dict[str, str]:
    """Strip empty / whitespace-only filter values into a clean dict."""
    raw = {
        "status": request.GET.get("status"),
        "severity": request.GET.get("severity"),
        "module": request.GET.get("module"),
        "environment": request.GET.get("environment"),
    }
    return {key: value.strip() for key, value in raw.items() if value and value.strip()}


def _viewer_permissions() -> dict[str, bool]:
    """Default viewer-permission flags for template rendering.

    v0.1 has no per-user permission abstraction — the flags here gate
    which buttons render, not who can press them. Consumers can override
    by patching :data:`VIEWER_PERMISSIONS` after import.
    """
    return dict(VIEWER_PERMISSIONS)


VIEWER_PERMISSIONS: dict[str, bool] = {
    "can_edit_status": True,
    "can_delete": True,
    "can_bulk": True,
}


def _compute_stats(storage: DjangoORMStorage) -> dict[str, int]:
    """Aggregate stat-card counts by status across non-archived reports."""
    stats: dict[str, int] = {}
    for state in ("open", "investigating", "fixed", "closed"):
        _, total = storage.list_reports({"status": state}, page=1, page_size=1)
        stats[state] = total
    _, total = storage.list_reports({}, page=1, page_size=1)
    stats["total"] = total
    return stats


# ---------------------------------------------------------------------------
# Intake — POST /bug-reports
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def intake_view(request: HttpRequest) -> HttpResponse:
    """Persist a new bug report per the v0.1 wire protocol.

    Accepts a multipart body with two parts:

    * ``metadata`` — JSON-encoded :class:`bug_fab.schemas.BugReportCreate`.
    * ``screenshot`` — PNG image bytes (validated by magic signature).

    Hands validation off to :func:`bug_fab.intake.validate_payload` so
    the protocol contract stays single-sourced. Successful submissions
    optionally fan out to GitHub Issues via
    :func:`bug_fab.adapters.django.github_sync.create_issue` (best-effort,
    failures logged not raised).
    """
    metadata_raw = request.POST.get("metadata")
    screenshot_file = request.FILES.get("screenshot")
    if not metadata_raw or screenshot_file is None:
        return _err("validation_error", "metadata and screenshot are both required", 400)

    screenshot_bytes = screenshot_file.read()
    try:
        validated = validate_payload(
            metadata_json=metadata_raw,
            screenshot_bytes=screenshot_bytes,
            screenshot_content_type=getattr(screenshot_file, "content_type", None) or "image/png",
            request_user_agent=request.META.get("HTTP_USER_AGENT", ""),
            max_screenshot_bytes=_max_upload_bytes(),
        )
    except PayloadTooLarge as exc:
        return _err("payload_too_large", str(exc), 413)
    except UnsupportedMediaType as exc:
        return _err("unsupported_media_type", str(exc), 415)
    except IntakeValidationError as exc:
        # JSON-decode failures carry an empty detail list; surface as 400
        # with the message. Schema failures carry the Pydantic error list
        # and surface as 422 to match the FastAPI envelope.
        if exc.detail:
            return _err("schema_error", exc.detail, 422)
        return _err("validation_error", str(exc), 400)

    # Build the persistence payload. Server-captured User-Agent wins over
    # the client value per ``docs/PROTOCOL.md`` § User-Agent trust boundary.
    payload = validated.metadata
    metadata_dict = payload.model_dump(mode="json")
    metadata_dict["server_user_agent"] = validated.user_agent
    metadata_dict["client_reported_user_agent"] = payload.context.user_agent
    metadata_dict["environment"] = (
        payload.context.environment or metadata_dict.get("environment", "") or ""
    )

    storage = _get_storage()
    try:
        report_id = storage.save_report(metadata_dict, validated.screenshot_bytes)
    except StorageError as exc:
        return _err("schema_error", str(exc), 422)
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_storage_save_failed")
        return _err("internal_error", "Failed to persist bug report", 500)

    detail = storage.get_report(report_id)
    received_at = detail.created_at if detail is not None else ""

    # Best-effort GitHub Issues sync. Imported lazily so consumers
    # without GitHub credentials don't import the optional dependency.
    github_issue_url: str | None = None
    try:
        from .github_sync import create_issue as _create_github_issue

        link = _create_github_issue(detail.model_dump(mode="json")) if detail else None
        if link is not None:
            github_issue_url = link.url
            storage.set_github_link(report_id, link.number, link.url)
    except Exception:  # pragma: no cover - defensive
        logger.exception("bug_fab_django_github_sync_failed", extra={"report_id": report_id})

    # Best-effort generic webhook delivery — fires AFTER GitHub sync so
    # ``github_issue_url`` (when present) rides along in the payload.
    # The webhook module is imported lazily so consumers who never
    # configure ``BUG_FAB_WEBHOOK_URL`` don't pay an import cost.
    if detail is not None:
        try:
            from .webhook_sync import send as _send_webhook

            payload = detail.model_dump(mode="json")
            if github_issue_url is not None:
                payload["github_issue_url"] = github_issue_url
            _send_webhook(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("bug_fab_django_webhook_sync_failed", extra={"report_id": report_id})

    return JsonResponse(
        {
            "id": report_id,
            "received_at": received_at,
            "stored_at": f"bug-fab-django://reports/{report_id}",
            "github_issue_url": github_issue_url,
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# Viewer — list + detail (HTML and JSON)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def report_list_html(request: HttpRequest) -> HttpResponse:
    """Render the HTML list page (stat cards + filters + table)."""
    storage = _get_storage()
    page = max(int(request.GET.get("page", "1") or 1), 1)
    page_size = max(min(int(request.GET.get("page_size", "20") or 20), 200), 1)
    filters = _build_filters(request)
    items, total = storage.list_reports(filters, page, page_size)
    stats = _compute_stats(storage)
    total_pages = max((total + page_size - 1) // page_size, 1)
    context = {
        "items": [item.model_dump() for item in items],
        "total": total,
        "stats": stats,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "filters": {
            "status": filters.get("status", ""),
            "severity": filters.get("severity", ""),
            "module": filters.get("module", ""),
            "environment": filters.get("environment", ""),
        },
        "permissions": _viewer_permissions(),
    }
    return render(request, "bug_fab/list.html", context)


@require_http_methods(["GET"])
def report_list_json(request: HttpRequest) -> HttpResponse:
    """Return a JSON page of report summaries with pagination metadata."""
    storage = _get_storage()
    page = max(int(request.GET.get("page", "1") or 1), 1)
    page_size = max(min(int(request.GET.get("page_size", "20") or 20), 200), 1)
    filters = _build_filters(request)
    items, total = storage.list_reports(filters, page, page_size)
    stats = _compute_stats(storage)
    return JsonResponse(
        {
            "items": [item.model_dump() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "stats": stats,
        }
    )


@require_http_methods(["GET"])
def report_detail_json(request: HttpRequest, report_id: str) -> HttpResponse:
    """Return the full JSON detail payload for a single report."""
    if not REPORT_ID_RE.match(report_id):
        return _err("not_found", "Bug report not found", 404)
    storage = _get_storage()
    report = storage.get_report(report_id)
    if report is None:
        return _err("not_found", "Bug report not found", 404)
    return JsonResponse(report.model_dump(mode="json"))


def _safe_context_url(report_dict: dict) -> str:
    """Return ``context.url`` only when its scheme is in the allowlist.

    Bug-Fab's intake doesn't validate the URL scheme on the way in (the
    field is a free-form string the bundle captures from the host page),
    so the viewer is responsible for refusing to render `javascript:`,
    `data:`, etc. as a clickable href. Per the v0.1.x security pass
    flagged in `SECURITY.md` § "no stored-XSS sinks" residual item.
    """
    ctx = report_dict.get("context") or {}
    url = ctx.get("url") or ""
    if isinstance(url, str) and url.startswith(("http://", "https://", "/")):
        return url
    return ""


@require_http_methods(["GET"])
def report_detail_html(request: HttpRequest, report_id: str) -> HttpResponse:
    """Render the HTML detail page (screenshot + metadata + lifecycle)."""
    if not REPORT_ID_RE.match(report_id):
        return _err("not_found", "Bug report not found", 404)
    storage = _get_storage()
    report = storage.get_report(report_id)
    if report is None:
        return _err("not_found", "Bug report not found", 404)
    report_dict = report.model_dump(mode="json")
    return render(
        request,
        "bug_fab/detail.html",
        {
            "report": report_dict,
            "safe_context_url": _safe_context_url(report_dict),
            "permissions": _viewer_permissions(),
        },
    )


@require_http_methods(["GET"])
def screenshot_view(request: HttpRequest, report_id: str) -> HttpResponse:
    """Serve the raw PNG bytes for a report's screenshot."""
    if not REPORT_ID_RE.match(report_id):
        return _err("not_found", "Bug report not found", 404)
    storage = _get_storage()
    path = storage.get_screenshot_path(report_id)
    if path is None:
        return _err("not_found", "Screenshot not found", 404)
    return FileResponse(open(path, "rb"), content_type="image/png")  # noqa: SIM115


# ---------------------------------------------------------------------------
# Viewer mutations — status update, delete, bulk
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["PUT"])
def status_update_view(request: HttpRequest, report_id: str) -> HttpResponse:
    """Update a report's status and append a lifecycle entry."""
    if not REPORT_ID_RE.match(report_id):
        return _err("not_found", "Bug report not found", 404)

    try:
        raw = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _err("validation_error", "request body must be JSON", 400)

    try:
        payload = BugReportStatusUpdate.model_validate(raw)
    except PydanticValidationError as exc:
        return _err("schema_error", exc.errors(), 422)

    storage = _get_storage()
    actor = _viewer_actor(request)
    try:
        updated = storage.update_status(
            report_id,
            status=payload.status.value,
            fix_commit=payload.fix_commit,
            fix_description=payload.fix_description,
            by=actor,
        )
    except StorageError as exc:
        return _err("schema_error", str(exc), 422)
    if updated is None:
        return _err("not_found", "Bug report not found", 404)

    # Best-effort GitHub state sync, mirroring the FastAPI viewer.
    if updated.github_issue_number:
        try:
            from .github_sync import sync_issue_state

            sync_issue_state(updated.github_issue_number, payload.status.value)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "bug_fab_django_github_state_sync_failed",
                extra={"report_id": report_id},
            )

    return JsonResponse(updated.model_dump(mode="json"))


@csrf_exempt
@require_http_methods(["DELETE"])
def delete_view(request: HttpRequest, report_id: str) -> HttpResponse:
    """Hard-delete a report and its screenshot."""
    if not REPORT_ID_RE.match(report_id):
        return _err("not_found", "Bug report not found", 404)
    storage = _get_storage()
    deleted = storage.delete_report(report_id)
    if not deleted:
        return _err("not_found", "Bug report not found", 404)
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def report_resource(request: HttpRequest, report_id: str) -> HttpResponse:
    """Dispatch ``/reports/{id}`` to the right per-method handler.

    The wire protocol overloads one path with two methods (``GET`` for
    JSON detail and ``DELETE`` for hard-delete). Django's URLconf binds
    a path to a single callable, so this view is the dispatch shim.
    """
    if request.method == "DELETE":
        return delete_view(request, report_id)
    return report_detail_json(request, report_id)


@csrf_exempt
@require_http_methods(["POST"])
def bulk_close_fixed_view(request: HttpRequest) -> HttpResponse:
    """Transition every ``fixed`` report to ``closed``."""
    storage = _get_storage()
    actor = _viewer_actor(request)
    closed = storage.bulk_close_fixed(by=actor)
    return JsonResponse({"closed": closed})


@csrf_exempt
@require_http_methods(["POST"])
def bulk_archive_closed_view(request: HttpRequest) -> HttpResponse:
    """Archive every ``closed`` report not already archived."""
    storage = _get_storage()
    archived = storage.bulk_archive_closed()
    return JsonResponse({"archived": archived})


# ---------------------------------------------------------------------------
# Catch-all dispatch helper for the viewer mount root.
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def bundle_view(request: HttpRequest) -> HttpResponse:
    """Serve the canonical Bug-Fab JS bundle.

    Avoids duplicating the bundle inside the Django app's ``static/``
    directory — the file is kept as the single canonical copy under
    ``bug_fab/static/`` (or its editable-install sibling) and streamed
    from disk on each request. Production deployments using a CDN can
    skip this view entirely and serve the bundle from collectstatic
    output instead.
    """
    bundle = _resolve_bundle_path()
    if bundle is None:
        return _err("not_found", "Bug-Fab static bundle not found", 404)
    return FileResponse(open(bundle, "rb"), content_type="application/javascript")  # noqa: SIM115


@require_http_methods(["GET"])
def viewer_root(request: HttpRequest) -> HttpResponse:
    """Dispatch the viewer mount root to the HTML list view.

    Exists as a named URL target so consumers can reverse-resolve the
    list page (``reverse("bug_fab:list")``) without depending on the
    function-based view's import path.
    """
    return report_list_html(request)


def _method_not_allowed(*methods: str) -> HttpResponseNotAllowed:
    """Tiny wrapper so the URLconf can express ``HttpResponseNotAllowed`` cleanly."""
    return HttpResponseNotAllowed(methods)
