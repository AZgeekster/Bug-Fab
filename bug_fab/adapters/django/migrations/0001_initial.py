"""Initial migration for the Bug-Fab Django reusable app.

Schema mirrors :mod:`bug_fab.adapters.django.models`. Generated as a
hand-written migration rather than ``makemigrations`` output so we can
ship it inside the source tree without depending on a managed
``django-admin`` invocation; the field definitions match the model
declarations exactly.
"""

from __future__ import annotations

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create the ``bug_fab_bugreport`` and ``bug_fab_bugreportlifecycle`` tables."""

    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="BugReport",
            fields=[
                (
                    "id",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                (
                    "received_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("protocol_version", models.CharField(default="0.1", max_length=16)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "severity",
                    models.CharField(blank=True, db_index=True, default="medium", max_length=16),
                ),
                (
                    "status",
                    models.CharField(db_index=True, default="open", max_length=16),
                ),
                (
                    "environment",
                    models.CharField(blank=True, db_index=True, default="", max_length=64),
                ),
                ("app_name", models.CharField(blank=True, default="", max_length=128)),
                (
                    "app_version",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("reporter", models.CharField(blank=True, default="", max_length=256)),
                ("page_url", models.TextField(blank=True, default="")),
                ("module", models.CharField(blank=True, default="", max_length=128)),
                ("user_agent_server", models.TextField(blank=True, default="")),
                ("user_agent_client", models.TextField(blank=True, default="")),
                ("metadata_json", models.TextField(blank=True, default="")),
                (
                    "screenshot",
                    models.FileField(max_length=512, upload_to="bug_fab_screenshots/"),
                ),
                (
                    "github_issue_url",
                    models.URLField(blank=True, default="", max_length=512),
                ),
                ("github_issue_number", models.IntegerField(blank=True, null=True)),
                (
                    "archived_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
            ],
            options={
                "verbose_name": "Bug report",
                "verbose_name_plural": "Bug reports",
                "ordering": ["-received_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="BugReportLifecycle",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("action", models.CharField(max_length=32)),
                ("by", models.CharField(blank=True, default="anonymous", max_length=256)),
                ("at", models.DateTimeField(default=django.utils.timezone.now)),
                ("fix_commit", models.CharField(blank=True, default="", max_length=512)),
                ("fix_description", models.TextField(blank=True, default="")),
                ("metadata_json", models.TextField(blank=True, default="")),
                (
                    "bug_report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lifecycle",
                        to="bug_fab.bugreport",
                    ),
                ),
            ],
            options={
                "ordering": ["at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="bugreport",
            index=models.Index(fields=["status", "severity"], name="bug_fab_bug_status_idx"),
        ),
        migrations.AddIndex(
            model_name="bugreportlifecycle",
            index=models.Index(fields=["bug_report", "at"], name="bug_fab_life_report_at_idx"),
        ),
    ]
