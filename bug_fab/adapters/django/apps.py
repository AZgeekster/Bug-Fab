"""Django ``AppConfig`` for the Bug-Fab reusable app.

Registered when consumers add ``"bug_fab.adapters.django"`` to
``INSTALLED_APPS``. The ``label`` attribute is locked to ``bug_fab`` so
table names, admin URLs, and template namespaces stay short and stable
even though the dotted module path is deep.

:meth:`BugFabConfig.ready` emits a one-time ``logging.warning`` when
``DATA_UPLOAD_MAX_MEMORY_SIZE`` is below the configured screenshot cap.
Django silently truncates request bodies above that setting *before*
intake views run — without the warning, the failure mode is a generic
``RequestDataTooBig`` that looks like an unrelated validation reject.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class BugFabConfig(AppConfig):
    """Reusable-app config for Bug-Fab inside a Django project.

    The dotted ``name`` is the import path; ``label`` is what shows up in
    DB table names (``bug_fab_bugreport``), template tag namespaces, and
    admin URLs (``/admin/bug_fab/bugreport/``). Keeping ``label`` short
    avoids the awkwardness of seeing
    ``bug_fab_adapters_django_bugreport`` in the admin sidebar.
    """

    name = "bug_fab.adapters.django"
    label = "bug_fab"
    verbose_name = "Bug-Fab"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Emit a one-time warning if the upload limit is misconfigured.

        ``DATA_UPLOAD_MAX_MEMORY_SIZE`` defaults to 2.5 MiB in Django,
        which silently truncates Bug-Fab screenshots before they reach
        the intake view. The protocol's ``413 payload_too_large`` shape
        is never returned in that scenario — instead, Django raises an
        opaque ``RequestDataTooBig`` and the consumer's logs show what
        looks like an unrelated reject. Surface the misconfiguration at
        app-startup time so it can be fixed before the first bug report
        hits production.
        """
        from django.conf import settings

        # Lazy import — ``views`` pulls in pydantic / storage / etc., and
        # we want ``ready()`` to stay cheap and free of import cycles.
        from .views import _max_upload_bytes

        screenshot_cap = _max_upload_bytes()
        data_upload_limit = getattr(settings, "DATA_UPLOAD_MAX_MEMORY_SIZE", None)
        if data_upload_limit is None or data_upload_limit >= screenshot_cap:
            return

        logger.warning(
            "DATA_UPLOAD_MAX_MEMORY_SIZE=%s is below Bug-Fab's screenshot "
            "cap of %s bytes. Django will silently truncate larger uploads "
            "before the intake view runs and the protocol's "
            "413 payload_too_large response will never be returned. "
            "Raise DATA_UPLOAD_MAX_MEMORY_SIZE (and FILE_UPLOAD_MAX_MEMORY_SIZE) "
            "to at least %s bytes in settings.py. See "
            "https://docs.djangoproject.com/en/stable/ref/settings/#data-upload-max-memory-size",
            data_upload_limit,
            screenshot_cap,
            screenshot_cap + 2 * 1024 * 1024,
        )
