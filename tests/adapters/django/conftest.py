"""Pytest configuration for the Django adapter test suite.

Configures Django with an in-memory SQLite database, registers the
Bug-Fab reusable app, runs migrations once per session, and exposes a
:class:`~django.test.Client` fixture pointed at the
:mod:`bug_fab.adapters.django.urls` URLconf.

The whole module is wrapped in :func:`pytest.importorskip` so the rest
of the test matrix (FastAPI, Flask, conformance) keeps passing on
machines that haven't installed the ``django`` extra. CI ensures
Django is available.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

django = pytest.importorskip("django")


def _configure_django() -> None:
    """Set up :mod:`django.conf.settings` once per process.

    Idempotent — calling twice is a no-op because Django itself raises
    on second :func:`django.conf.settings.configure`. The settings dict
    is the minimum viable config: in-memory SQLite for speed, a
    TempDir-based ``MEDIA_ROOT`` so screenshot writes don't litter the
    repo, and the standard auth / admin apps so the auth helpers in
    :mod:`bug_fab.adapters.django.auth` import without errors.
    """
    from django.conf import settings

    if settings.configured:
        return

    media_root = Path(tempfile.mkdtemp(prefix="bug_fab_django_test_"))
    settings.configure(
        DEBUG=False,
        SECRET_KEY="bug-fab-django-tests-only",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "bug_fab.adapters.django",
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ],
                },
            }
        ],
        ROOT_URLCONF="bug_fab.adapters.django.urls",
        MEDIA_ROOT=str(media_root),
        MEDIA_URL="/media/",
        STATIC_URL="/static/",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        DATA_UPLOAD_MAX_MEMORY_SIZE=12 * 1024 * 1024,
        FILE_UPLOAD_MAX_MEMORY_SIZE=12 * 1024 * 1024,
        USE_TZ=True,
        TIME_ZONE="UTC",
    )

    django.setup()


_configure_django()


@pytest.fixture(scope="session", autouse=True)
def _django_schema_setup():
    """Create the schema once per session on the in-memory SQLite DB."""
    from django.core.management import call_command

    call_command("migrate", verbosity=0, interactive=False, run_syncdb=True)


@pytest.fixture(autouse=True)
def _django_db_clear(request, _django_schema_setup):
    """Wipe ``BugReport`` rows + screenshots between tests.

    Lets each test see a clean slate without the cost of dropping and
    recreating the schema. SQLite ``:memory:`` only lives as long as the
    connection, so a session-scoped schema + per-test row deletion is the
    cheapest reliable reset.
    """
    yield
    # Late imports — the model module needs Django to be configured.
    from bug_fab.adapters.django.models import BugReport, BugReportLifecycle

    # Cascade handles lifecycle, but be explicit so a missed FK doesn't
    # leave orphan rows that confuse the next test's assertions.
    BugReportLifecycle.objects.all().delete()
    BugReport.objects.all().delete()


@pytest.fixture()
def client():
    """Return a Django test client pointed at the combined URLconf."""
    from django.test import Client

    return Client()


@pytest.fixture()
def png_bytes() -> bytes:
    """Return a minimal valid PNG (8-byte signature + IHDR + IDAT + IEND).

    Hand-crafted so the tests don't depend on Pillow being installed.
    """
    # 1x1 transparent PNG — standard reference bytes.
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.fixture()
def metadata_json() -> str:
    """Return a valid intake metadata payload as a JSON string."""
    import json

    return json.dumps(
        {
            "protocol_version": "0.1",
            "title": "Save button is unresponsive",
            "client_ts": "2026-04-27T15:29:58-07:00",
            "report_type": "bug",
            "description": "Click does nothing on the cart page.",
            "expected_behavior": "Cart should save and proceed to checkout.",
            "severity": "high",
            "tags": ["regression", "checkout"],
            "reporter": {"email": "alice@example.com"},
            "context": {
                "url": "https://example.com/cart",
                "module": "checkout",
                "user_agent": "Mozilla/5.0 (test client)",
                "viewport_width": 1920,
                "viewport_height": 1080,
                "app_version": "1.4.2",
                "environment": "prod",
                "console_errors": [],
                "network_log": [],
            },
        }
    )
