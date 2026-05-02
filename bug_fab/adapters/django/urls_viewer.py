"""Viewer URLconf — list / detail / mutation endpoints.

Per ``docs/PROTOCOL.md`` § Viewer mount-prefix note, the viewer prefix
MUST be non-empty (the HTML list view renders at the prefix root).
Mount under the prefix the consumer's admin auth covers — typically
``/admin/bug-reports/`` for staff-only access.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "bug_fab_viewer"

urlpatterns = [
    path("", views.viewer_root, name="root"),
    path("reports", views.report_list_json, name="list_json"),
    # Single path handling GET (JSON detail) and DELETE (hard-delete) per
    # the wire protocol. Method dispatch happens in :func:`views.report_resource`.
    path("reports/<str:report_id>", views.report_resource, name="detail_json"),
    path(
        "reports/<str:report_id>/screenshot",
        views.screenshot_view,
        name="screenshot",
    ),
    path(
        "reports/<str:report_id>/status",
        views.status_update_view,
        name="status",
    ),
    path("bulk-close-fixed", views.bulk_close_fixed_view, name="bulk_close_fixed"),
    path(
        "bulk-archive-closed",
        views.bulk_archive_closed_view,
        name="bulk_archive_closed",
    ),
    # HTML detail page lives under the prefix root — keep it last so the
    # static-segment routes above (``reports``, ``bulk-...``) match first.
    path("<str:report_id>", views.report_detail_html, name="detail"),
]
