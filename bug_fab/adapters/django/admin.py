"""Django admin registration for Bug-Fab models.

Gives consumers a free admin UI for triage. The Bug-Fab viewer at
``/admin/bug-reports/`` is the operator-facing experience, but the
plain Django admin is useful for ad-hoc data inspection and exports.
"""

from __future__ import annotations

from django.contrib import admin

from .models import BugReport, BugReportLifecycle


class BugReportLifecycleInline(admin.TabularInline):
    """Inline lifecycle entries on the bug-report change page.

    ``extra = 0`` keeps the form compact — admins shouldn't add
    lifecycle entries by hand; they're produced by the views layer.
    """

    model = BugReportLifecycle
    extra = 0
    can_delete = False
    readonly_fields = ("action", "by", "at", "fix_commit", "fix_description")
    fields = readonly_fields


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    """Admin config for the bug-report change list."""

    list_display = (
        "id",
        "title",
        "status",
        "severity",
        "environment",
        "received_at",
        "github_issue_number",
    )
    list_filter = ("status", "severity", "environment", "archived_at")
    search_fields = ("id", "title", "description", "module", "reporter")
    readonly_fields = (
        "id",
        "received_at",
        "protocol_version",
        "user_agent_server",
        "user_agent_client",
        "metadata_json",
        "screenshot",
    )
    inlines = [BugReportLifecycleInline]
    ordering = ("-received_at", "-id")


@admin.register(BugReportLifecycle)
class BugReportLifecycleAdmin(admin.ModelAdmin):
    """Admin config for the standalone lifecycle change list (read-only)."""

    list_display = ("bug_report", "action", "by", "at")
    list_filter = ("action",)
    search_fields = ("bug_report__id", "by", "fix_commit")
    readonly_fields = (
        "bug_report",
        "action",
        "by",
        "at",
        "fix_commit",
        "fix_description",
        "metadata_json",
    )
    ordering = ("-at", "-id")
