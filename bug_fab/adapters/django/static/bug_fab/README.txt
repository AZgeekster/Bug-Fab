Bug-Fab Django adapter — static bundle notes
=============================================

The canonical bug-fab.js bundle lives in the top-level package static
directory at <site-packages>/bug_fab/static/bug-fab.js, NOT here. The
Django adapter serves it via the bundle_view route registered in
urls_intake.py at <prefix>/bug-fab/static/bug-fab.js.

This avoids duplicating the bundle inside the Django app and keeps the
single source of truth in line with the CDN-friendly file layout.

Production deployments using collectstatic + a CDN can skip the
bundle_view route and serve bug-fab.js directly from STATIC_ROOT after
running ``./manage.py collectstatic`` over the package's static dir.
