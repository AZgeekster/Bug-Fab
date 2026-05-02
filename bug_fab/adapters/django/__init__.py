"""Django adapter for Bug-Fab — install via ``pip install bug-fab[django]``.

A reusable Django app that consumers register in ``INSTALLED_APPS`` and
mount via ``include()`` in their root ``urls.py``. Three steps and the
v0.1 wire protocol is live::

    # settings.py
    INSTALLED_APPS = [
        ...,
        "bug_fab.adapters.django",
    ]
    MEDIA_ROOT = BASE_DIR / "media"
    DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024  # raise above the 10 MiB cap

    # urls.py
    from django.urls import include, path
    urlpatterns = [
        path("api/",   include("bug_fab.adapters.django.urls_intake")),
        path("admin/bug-reports/", include("bug_fab.adapters.django.urls_viewer")),
    ]

    # one-time
    python manage.py migrate

The intake router is mounted under whatever prefix the consumer's
existing public auth covers (typically ``/api/``); the viewer router is
mounted under whatever prefix the consumer's admin auth covers
(typically ``/admin/...``). v0.1 has no auth abstraction — protection is
mount-prefix-delegated, exactly as it is for the FastAPI reference.

Why this is a *parallel* implementation rather than a thin shim over
:class:`bug_fab.storage.Storage`: Django's ORM is synchronous and
session-bound, and bridging it to the async ABC would either require
``async_to_sync`` round-trips per call or duplicate every Storage method.
A native Django ORM storage class (:class:`DjangoORMStorage`) is simpler
and matches the framework's idioms — admin integration, migrations,
``select_for_update``, etc., all work out of the box. Validation still
flows through :func:`bug_fab.intake.validate_payload` so the protocol
contract is shared.
"""

from __future__ import annotations

default_app_config = "bug_fab.adapters.django.apps.BugFabConfig"

__all__ = ["default_app_config"]
