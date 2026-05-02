"""Root URLconf for the Bug-Fab Django minimal example."""

from __future__ import annotations

from django.http import HttpResponse
from django.urls import include, path


def home(request):
    """Render a tiny HTML stub that loads the Bug-Fab JS bundle."""
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Bug-Fab Django demo</title>
  </head>
  <body>
    <h1>Bug-Fab Django demo</h1>
    <p>Click the floating bug icon to submit a report.</p>
    <p>Viewer: <a href="/admin/bug-reports/">/admin/bug-reports/</a></p>
    <script src="/api/bug-fab/static/bug-fab.js" defer></script>
    <script>
      window.addEventListener("DOMContentLoaded", function () {
        window.BugFab.init({ submitUrl: "/api/bug-reports" });
      });
    </script>
  </body>
</html>"""
    return HttpResponse(html)


urlpatterns = [
    path("", home),
    # Open submit endpoint plus the JS bundle convenience route.
    path("api/", include("bug_fab.adapters.django.urls_intake")),
    # Auth-gated viewer — wrap with the consumer's admin middleware.
    path("admin/bug-reports/", include("bug_fab.adapters.django.urls_viewer")),
]
