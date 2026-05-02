"""Combined URLconf — convenience entry-point that mounts both routers.

Most consumers use the split URLconfs (:mod:`urls_intake` for the open
submit endpoint, :mod:`urls_viewer` for the auth-gated viewer) so they
can apply different middleware to each. This module is the simpler
"mount everything under one prefix" path for POCs and internal tools::

    # urls.py
    urlpatterns = [
        path("bug-fab/", include("bug_fab.adapters.django.urls")),
    ]
"""

from __future__ import annotations

from django.urls import include, path

app_name = "bug_fab"

urlpatterns = [
    path("", include("bug_fab.adapters.django.urls_intake")),
    path("", include("bug_fab.adapters.django.urls_viewer")),
]
