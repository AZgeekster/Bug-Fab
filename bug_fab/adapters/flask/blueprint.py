"""Flask Blueprint factory implementing the full Bug-Fab v0.1 wire protocol.

The factory :func:`make_blueprint` returns a ready-to-mount
:class:`flask.Blueprint` that exposes every endpoint defined in
``docs/PROTOCOL.md`` § Endpoints plus the HTML viewer pages and the
static bundle. A consumer's integration code drops to::

    from bug_fab.adapters.flask import make_blueprint
    from bug_fab.config import Settings
    app.register_blueprint(make_blueprint(Settings()), url_prefix="/bug-fab")

Mount-prefix requirement
------------------------
The viewer's HTML list page lives at the blueprint's *root* path
(``GET ""``) and the detail page lives at ``GET /<report_id>``. Mounting
without a non-empty ``url_prefix`` would put both at the host
application's root and conflict with the consumer's own routes. Every
example in this repo mounts under a non-empty prefix; do the same.

Validation reuse
----------------
Per ``CLAUDE.md`` § Anti-patterns, this module does NOT re-implement
intake validation, does NOT define its own Pydantic models, and does
NOT couple to a specific :class:`Storage` backend. Validation flows
through :func:`bug_fab.intake.validate_payload`; persistence flows
through whatever :class:`Storage` the consumer wires in (default:
:class:`bug_fab.storage.FileStorage` rooted at ``settings.storage_dir``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from flask import Blueprint, Response, abort, jsonify, render_template, request, send_from_directory
from pydantic import ValidationError

from bug_fab.adapters.flask._runtime import (
    resolve_static_dir,
    resolve_template_dir,
    run_sync,
)
from bug_fab.config import Settings
from bug_fab.intake import (
    IntakeError,
    PayloadTooLarge,
    UnsupportedMediaType,
    validate_payload,
)
from bug_fab.intake import (
    ValidationError as IntakeValidationError,
)
from bug_fab.integrations.github import GitHubSync
from bug_fab.schemas import BugReportStatusUpdate
from bug_fab.storage.base import Storage
from bug_fab.storage.files import FileStorage

logger = logging.getLogger(__name__)

#: Path-traversal guard — mirrors :data:`bug_fab.routers.viewer._REPORT_ID_RE`.
#: Any input outside this character class is rejected with 404 before it
#: reaches the storage layer.
_REPORT_ID_RE = re.compile(r"^bug-[A-Za-z]?\d{1,12}$")


def _error(code: str, detail: Any, status_code: int, **extra: Any) -> tuple[Response, int]:
    """Return the protocol-standard error envelope as a Flask response.

    Per ``docs/PROTOCOL.md`` § Error responses, every non-2xx body has
    the same ``{error, detail}`` shape. Extra fields (e.g. ``limit_bytes``
    on 413) ride alongside via ``**extra``.
    """
    body: dict[str, Any] = {"error": code, "detail": detail}
    body.update(extra)
    return jsonify(body), status_code


def _validate_report_id(report_id: str) -> None:
    """Reject IDs that fail the ``bug-NNN`` shape guard with a 404."""
    if not _REPORT_ID_RE.match(report_id):
        abort(404)


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


def _compute_stats(storage: Storage) -> dict[str, int]:
    """Aggregate stat-card counts from the storage backend.

    Mirrors :func:`bug_fab.routers.viewer._compute_stats` so the Flask
    viewer renders the same five-card stat row the FastAPI reference
    does. Each filter is one ``list_reports`` call with ``page_size=1``
    purely for the total — the items are discarded.
    """
    stats: dict[str, int] = {}
    for state in ("open", "investigating", "fixed", "closed"):
        _, total = run_sync(storage.list_reports({"status": state}, page=1, page_size=1))
        stats[state] = total
    _, total = run_sync(storage.list_reports({}, page=1, page_size=1))
    stats["total"] = total
    return stats


def _resolve_csp_nonce(settings: Settings) -> str | None:
    """Invoke the configured nonce provider, swallowing failures.

    A misbehaving provider (raises, returns a non-string) must not crash
    the viewer page render — CSP integration is opt-in glue and the safe
    fallback is to render without a nonce attribute. Mirrors the FastAPI
    viewer's behavior so consumers running mixed adapters see consistent
    CSP-failure semantics.
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


def _viewer_actor() -> str:
    """Best-effort actor identifier for the lifecycle log.

    Bug-Fab v0.1 has no ``AuthAdapter``, so this defers to whatever the
    consumer's middleware stashed on the Flask request object. Matches
    the FastAPI viewer's ``request.state.bug_fab_actor`` convention.
    """
    actor = getattr(request, "bug_fab_actor", None)
    return str(actor) if actor else "viewer"


def _require_permission(settings: Settings, flag: str) -> None:
    """Raise 403 when the named viewer permission is disabled.

    Mirrors :func:`bug_fab.routers.viewer._permission_dep`. The flag
    names (``can_edit_status``, ``can_delete``, ``can_bulk``) are the
    same three keys the FastAPI viewer gates on.
    """
    if not settings.viewer_permissions.get(flag, False):
        abort(403, description=f"viewer action '{flag}' is disabled by configuration")


def make_blueprint(
    settings: Settings,
    *,
    storage: Storage | None = None,
    github_sync: GitHubSync | None = None,
    name: str = "bug_fab",
) -> Blueprint:
    """Build a Flask ``Blueprint`` exposing the full Bug-Fab wire protocol.

    Parameters
    ----------
    settings:
        The :class:`bug_fab.config.Settings` instance the consumer's app
        already builds. Drives the storage location, max upload size,
        viewer permissions, and CSP nonce hook.
    storage:
        Optional explicit :class:`Storage` instance. When ``None``,
        :class:`bug_fab.storage.FileStorage` is constructed at
        ``settings.storage_dir``. Pass an explicit instance to use
        :class:`SQLiteStorage`, :class:`PostgresStorage`, or a contrib
        backend — the blueprint never instantiates one of those itself
        because their optional deps would leak into the Flask install.
    name:
        Override the Blueprint name (the default ``"bug_fab"`` is fine
        unless the consumer registers two separate Bug-Fab blueprints
        in the same app, which is unusual).

    Returns
    -------
    flask.Blueprint
        Mount it via ``app.register_blueprint(bp, url_prefix="/bug-fab")``.
        The prefix MUST be non-empty — see this module's mount-prefix note.

    Notes
    -----
    The blueprint is async-bridged via :func:`asyncio.run` per request.
    See :mod:`bug_fab.adapters.flask._runtime` for the trade-off
    discussion. The conformance suite passes with this bridge.
    """
    if storage is None:
        storage = FileStorage(storage_dir=settings.storage_dir, id_prefix=settings.id_prefix)

    # Build the GitHub sync the same way the FastAPI router's `configure()`
    # does (see :mod:`bug_fab.routers.submit`): only enabled when settings
    # opt in AND a PAT + repo are configured. Consumers can override by
    # passing an explicit instance (useful for tests injecting a fake).
    if (
        github_sync is None
        and settings.github_enabled
        and settings.github_pat
        and settings.github_repo
    ):
        github_sync = GitHubSync(
            pat=settings.github_pat,
            repo=settings.github_repo,
            api_base=settings.github_api_base,
        )

    template_dir = resolve_template_dir()
    static_dir = resolve_static_dir()

    bp = Blueprint(
        name,
        __name__,
        template_folder=str(template_dir),
        # The blueprint serves static via a manual route (below) so we
        # can reuse the on-disk path the FastAPI adapter ships from. The
        # built-in ``static_folder`` machinery would fight with that.
        static_folder=None,
    )

    # ------------------------------------------------------------------
    # Errorhandlers — convert Flask's default HTML 404/403 pages into the
    # protocol's ``{error, detail}`` JSON envelope so every non-2xx body
    # has the same shape per ``docs/PROTOCOL.md`` § Error responses.
    # ``abort(404)`` and ``abort(403)`` inside route handlers are caught
    # here; without these handlers Flask returns its built-in HTML page.
    # ------------------------------------------------------------------
    @bp.errorhandler(404)
    def _bug_fab_not_found(exc: Any) -> tuple[Response, int]:
        detail = getattr(exc, "description", None) or "Resource not found"
        return _error("not_found", detail, 404)

    @bp.errorhandler(403)
    def _bug_fab_forbidden(exc: Any) -> tuple[Response, int]:
        detail = getattr(exc, "description", None) or "Forbidden"
        return _error("forbidden", detail, 403)

    # ------------------------------------------------------------------
    # POST /bug-reports — submit (intake)
    # ------------------------------------------------------------------
    @bp.post("/bug-reports")
    def submit_bug_report() -> tuple[Response, int]:
        """Persist a new bug report per ``docs/PROTOCOL.md`` § Intake."""
        metadata_raw = request.form.get("metadata")
        screenshot_file = request.files.get("screenshot")
        if not metadata_raw or screenshot_file is None:
            return _error("validation_error", "metadata and screenshot are both required", 400)

        screenshot_bytes = screenshot_file.read()
        if not screenshot_bytes:
            return _error("validation_error", "Screenshot file is empty", 400)

        # Reuse the framework-agnostic validator so this adapter stays
        # protocol-conformant by construction. ``validate_payload``
        # raises typed :class:`IntakeError` subclasses we map onto the
        # protocol's HTTP envelope below.
        max_bytes = settings.max_upload_mb * 1024 * 1024
        try:
            validated = validate_payload(
                metadata_json=metadata_raw,
                screenshot_bytes=screenshot_bytes,
                screenshot_content_type=(screenshot_file.mimetype or "image/png"),
                request_user_agent=request.headers.get("User-Agent"),
                max_screenshot_bytes=max_bytes,
            )
        except PayloadTooLarge as exc:
            return _error("payload_too_large", exc.message, 413, limit_bytes=max_bytes)
        except UnsupportedMediaType as exc:
            return _error("unsupported_media_type", exc.message, 415)
        except IntakeValidationError as exc:
            # Pydantic errors land in ``exc.detail``; JSON-decode failures
            # leave it empty and put the message on ``exc.message``.
            if exc.detail:
                return _error("schema_error", exc.detail, 422)
            return _error("validation_error", exc.message, 400)
        except IntakeError as exc:  # pragma: no cover - defensive catch-all
            return _error(exc.code, exc.message, exc.status_code)

        # The server is authoritative for User-Agent, environment, and
        # the protocol-version tag — mirrors :mod:`bug_fab.routers.submit`
        # so a report submitted through Flask round-trips identical to
        # one submitted through FastAPI.
        metadata_dict = validated.metadata.model_dump(mode="json")
        metadata_dict["server_user_agent"] = validated.user_agent
        metadata_dict["client_reported_user_agent"] = validated.metadata.context.user_agent
        try:
            metadata_obj_raw = json.loads(metadata_raw)
        except json.JSONDecodeError:  # pragma: no cover - already validated
            metadata_obj_raw = {}
        metadata_dict["environment"] = (
            validated.metadata.context.environment or metadata_obj_raw.get("environment") or ""
        )

        try:
            report_id = run_sync(storage.save_report(metadata_dict, screenshot_bytes))
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except Exception:  # pragma: no cover - defensive
            logger.exception("bug_fab_storage_save_failed")
            return _error("internal_error", "Failed to persist bug report", 500)

        detail = run_sync(storage.get_report(report_id))
        if detail is None:  # pragma: no cover - storage contract violation
            return _error("internal_error", "Stored report could not be read back", 500)

        # Best-effort GitHub Issues sync — mirrors
        # :mod:`bug_fab.routers.submit` (lines 247-259). A failed POST does
        # NOT roll back the local save; the report just lacks a
        # ``github_issue_url`` until a manual cross-link or replay.
        github_issue_url: str | None = detail.github_issue_url
        if github_sync is not None:
            try:
                issue_number, issue_url = run_sync(
                    github_sync.create_issue(detail.model_dump(mode="json"))
                )
                if issue_number is not None and issue_url is not None:
                    github_issue_url = issue_url
                    run_sync(storage.set_github_link(report_id, issue_number, issue_url))
            except Exception:  # pragma: no cover - defensive
                logger.exception("bug_fab_github_sync_failed", extra={"report_id": report_id})

        # Per PROTOCOL.md § Response — minimal envelope, NOT the full
        # BugReportDetail. ``stored_at`` is opaque; consumers wanting the
        # full stored shape do GET /reports/{id} after.
        return jsonify(
            {
                "id": report_id,
                "received_at": detail.created_at,
                "stored_at": f"bug-fab://reports/{report_id}",
                "github_issue_url": github_issue_url,
            }
        ), 201

    # ------------------------------------------------------------------
    # GET "" — HTML viewer list page
    # ------------------------------------------------------------------
    @bp.get("/")
    def list_reports_html() -> str:
        """Render the HTML list page with stat cards and filters."""
        page = max(request.args.get("page", default=1, type=int) or 1, 1)
        page_size_q = request.args.get("page_size", type=int)
        effective_page_size = (
            min(page_size_q, 200) if page_size_q and page_size_q > 0 else settings.viewer_page_size
        )
        filters = _build_filters(
            status=request.args.get("status"),
            severity=request.args.get("severity"),
            module=request.args.get("module"),
            environment=request.args.get("environment"),
        )
        items, total = run_sync(storage.list_reports(filters, page, effective_page_size))
        stats = _compute_stats(storage)
        total_pages = max((total + effective_page_size - 1) // effective_page_size, 1)
        return render_template(
            "list.html",
            items=items,
            total=total,
            stats=stats,
            page=page,
            page_size=effective_page_size,
            total_pages=total_pages,
            filters={
                "status": filters.get("status", ""),
                "severity": filters.get("severity", ""),
                "module": filters.get("module", ""),
                "environment": filters.get("environment", ""),
            },
            permissions=settings.viewer_permissions,
            csp_nonce=_resolve_csp_nonce(settings),
        )

    # ------------------------------------------------------------------
    # GET /reports — JSON list
    # ------------------------------------------------------------------
    @bp.get("/reports")
    def list_reports_json() -> Response:
        """Return a paginated JSON list of bug-report summaries."""
        page = max(request.args.get("page", default=1, type=int) or 1, 1)
        page_size_q = request.args.get("page_size", type=int)
        effective_page_size = (
            min(page_size_q, 200) if page_size_q and page_size_q > 0 else settings.viewer_page_size
        )
        filters = _build_filters(
            status=request.args.get("status"),
            severity=request.args.get("severity"),
            module=request.args.get("module"),
            environment=request.args.get("environment"),
        )
        items, total = run_sync(storage.list_reports(filters, page, effective_page_size))
        # Compute stats for the protocol's documented response shape.
        stats = _compute_stats(storage)
        # The protocol's list response includes ``stats`` — see
        # PROTOCOL.md § GET /reports. The FastAPI reference's
        # BugReportListResponse model omits it (a v0.1 known-gap); the
        # Flask adapter ships the protocol-correct shape so adapter
        # authors pointing at this output see the right fields.
        return jsonify(
            {
                "items": [item.model_dump(mode="json") for item in items],
                "total": total,
                "page": page,
                "page_size": effective_page_size,
                "stats": {k: stats.get(k, 0) for k in ("open", "investigating", "fixed", "closed")},
            }
        )

    # ------------------------------------------------------------------
    # GET /reports/<id> — JSON detail
    # ------------------------------------------------------------------
    @bp.get("/reports/<report_id>")
    def get_report_json(report_id: str) -> Response:
        """Return the full JSON detail payload for a single report."""
        _validate_report_id(report_id)
        report = run_sync(storage.get_report(report_id))
        if report is None:
            abort(404)
        return jsonify(report.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # GET /reports/<id>/screenshot — raw PNG
    # ------------------------------------------------------------------
    @bp.get("/reports/<report_id>/screenshot")
    def get_screenshot(report_id: str) -> Response:
        """Return the report's screenshot image as raw PNG bytes."""
        _validate_report_id(report_id)
        path = run_sync(storage.get_screenshot_path(report_id))
        if path is None:
            abort(404)
        # ``send_from_directory`` is the safest Flask primitive — it
        # rejects path-escapes inside the directory and sets the right
        # ``Content-Type`` header.
        return send_from_directory(str(path.parent), path.name, mimetype="image/png")

    # ------------------------------------------------------------------
    # PUT /reports/<id>/status — status update
    # ------------------------------------------------------------------
    @bp.put("/reports/<report_id>/status")
    def update_report_status(report_id: str) -> Response | tuple[Response, int]:
        """Apply a status change, append a lifecycle entry, sync to GitHub."""
        _require_permission(settings, "can_edit_status")
        _validate_report_id(report_id)
        try:
            payload = BugReportStatusUpdate.model_validate(request.get_json(silent=True) or {})
        except ValidationError as exc:
            return _error("schema_error", exc.errors(), 422)

        actor = _viewer_actor()
        try:
            updated = run_sync(
                storage.update_status(
                    report_id,
                    status=payload.status.value,
                    fix_commit=payload.fix_commit,
                    fix_description=payload.fix_description,
                    by=actor,
                )
            )
        except ValueError as exc:
            return _error("schema_error", str(exc), 422)
        if updated is None:
            abort(404)

        # Best-effort GitHub state sync — mirrors
        # :mod:`bug_fab.routers.viewer` lines 295-300. open/investigating
        # reopens the issue; fixed/closed closes it. Failures only log.
        if github_sync is not None and updated.github_issue_number:
            try:
                run_sync(
                    github_sync.sync_issue_state(updated.github_issue_number, payload.status.value)
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("bug_fab_github_state_sync_failed", extra={"report_id": report_id})

        return jsonify(updated.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # DELETE /reports/<id>
    # ------------------------------------------------------------------
    @bp.delete("/reports/<report_id>")
    def delete_report(report_id: str) -> tuple[str, int]:
        """Hard-delete the metadata + screenshot for a single report."""
        _require_permission(settings, "can_delete")
        _validate_report_id(report_id)
        deleted = run_sync(storage.delete_report(report_id))
        if not deleted:
            abort(404)
        return "", 204

    # ------------------------------------------------------------------
    # POST /bulk-close-fixed
    # ------------------------------------------------------------------
    @bp.post("/bulk-close-fixed")
    def bulk_close_fixed() -> Response:
        """Transition every ``fixed`` report to ``closed``."""
        _require_permission(settings, "can_bulk")
        actor = _viewer_actor()
        closed = run_sync(storage.bulk_close_fixed(by=actor))
        return jsonify({"closed": closed})

    # ------------------------------------------------------------------
    # POST /bulk-archive-closed
    # ------------------------------------------------------------------
    @bp.post("/bulk-archive-closed")
    def bulk_archive_closed() -> Response:
        """Move every ``closed`` report into the storage backend's archive area."""
        _require_permission(settings, "can_bulk")
        archived = run_sync(storage.bulk_archive_closed())
        return jsonify({"archived": archived})

    # ------------------------------------------------------------------
    # GET /<report_id> — HTML detail page
    # NOTE: registered LAST so the more-specific routes above
    # (``/reports``, ``/bulk-*``, ``/bug-reports``) win the routing
    # decision — Flask's URL map prefers static prefixes over
    # converters, but ordering here doubles as a readability hint.
    # ------------------------------------------------------------------
    @bp.get("/<report_id>")
    def get_report_html(report_id: str) -> str:
        """Render the HTML detail page (screenshot + metadata + lifecycle)."""
        _validate_report_id(report_id)
        report = run_sync(storage.get_report(report_id))
        if report is None:
            abort(404)
        return render_template(
            "detail.html",
            report=report,
            permissions=settings.viewer_permissions,
            csp_nonce=_resolve_csp_nonce(settings),
        )

    # ------------------------------------------------------------------
    # GET /static/<path> — serve the bundled JS / CSS / html2canvas
    # ------------------------------------------------------------------
    @bp.get("/static/<path:filename>")
    def bug_fab_static(filename: str) -> Response:
        """Serve the vendored Bug-Fab frontend bundle byte-for-byte."""
        return send_from_directory(str(static_dir), filename)

    return bp


# Re-exported so adapter authors writing custom routes can reach the same
# helpers without redefining them.
__all__: list[str] = ["make_blueprint"]
