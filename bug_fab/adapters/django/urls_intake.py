"""Intake URLconf — ``POST /bug-reports``.

Mount under whatever prefix the consumer's public-but-rate-limited
auth covers — typically ``/api/`` so the bundled JS frontend can submit
without an admin login.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "bug_fab_intake"

urlpatterns = [
    path("bug-reports", views.intake_view, name="intake"),
    # Convenience route that streams the canonical JS bundle from the
    # package's ``static/`` directory. Production deployments using a
    # CDN can ignore this and serve the bundle from collectstatic
    # output instead.
    path("bug-fab/static/bug-fab.js", views.bundle_view, name="bundle"),
]
