"""Django ``AppConfig`` for the Bug-Fab reusable app.

Registered when consumers add ``"bug_fab.adapters.django"`` to
``INSTALLED_APPS``. The ``label`` attribute is locked to ``bug_fab`` so
table names, admin URLs, and template namespaces stay short and stable
even though the dotted module path is deep.
"""

from __future__ import annotations

from django.apps import AppConfig


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
