"""Pydantic v2 schemas for the Bug-Fab wire protocol.

These models define the payload shape for:
- Submission intake (`BugReportCreate` + `BugReportContext`)
- Status updates (`BugReportStatusUpdate`)
- Read responses (`BugReportSummary`, `BugReportDetail`, `BugReportListResponse`)
- Lifecycle audit entries (`LifecycleEvent`)

Severity and Status are real string enums (subclasses of `str` + `enum.Enum`)
so JSON round-trips and adapter validation share one source of truth.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    """Locked severity vocabulary — adapters MUST reject other values with 422.

    WHY str-Enum (not StrEnum): Python 3.10 support is required, and StrEnum
    was added in 3.11. Subclassing both `str` and `Enum` produces equivalent
    JSON behavior on the older interpreter.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Status(str, Enum):
    """Locked status vocabulary for the lifecycle workflow.

    WHY four values: matches every audited prior implementation. Deprecated
    values (e.g., legacy `resolved`) MUST still parse on read per protocol;
    write-side validation is strict.
    """

    OPEN = "open"
    INVESTIGATING = "investigating"
    FIXED = "fixed"
    CLOSED = "closed"


class Reporter(BaseModel):
    """Optional submitter identity attached to a bug report.

    All fields are opaque strings, capped at 256 characters per the
    2026-04-28 spec-gap decisions (per `docs/decisions.md`). The protocol
    does not validate the format of these values — consumer user IDs vary
    widely (UUIDs, emails, integers-as-strings, SSO subjects).

    Adapters MAY override the client-supplied values with server-derived
    identity when they have their own auth context.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="", max_length=256)
    email: str = Field(default="", max_length=256)
    user_id: str = Field(default="", max_length=256)


class BugReportContext(BaseModel):
    """Auto-captured browser context attached to every submission.

    `source_mapping` is consumer-supplied opaque metadata; the server does
    NOT compute URL→file mappings (that responsibility moved to the client
    or to a consumer-provided resolver hook in v0.2+).

    `user_agent` here is the *client-reported* value, kept for diagnostics.
    The adapter captures the request-header User-Agent separately as the
    source of truth (`server_user_agent` on `BugReportDetail`).
    """

    model_config = ConfigDict(extra="allow")

    url: str = ""
    module: str = ""
    user_agent: str = ""
    viewport_width: int = 0
    viewport_height: int = 0
    console_errors: list[dict[str, Any]] = Field(default_factory=list)
    network_log: list[dict[str, Any]] = Field(default_factory=list)
    source_mapping: dict[str, Any] = Field(default_factory=dict)
    app_version: str = ""
    environment: str = ""


class BugReportCreate(BaseModel):
    """Submission payload sent as a JSON string in the multipart `metadata` field.

    `report_type` is a `Literal` (not enum) because the protocol freezes the
    two values and there is no read-side deprecation concern.

    `protocol_version` is required and must equal `"0.1"`. Future versions
    bump this. Adapters MUST reject unknown values with `400 unsupported_protocol_version`.

    `client_ts` is a client-side ISO-8601 timestamp captured when the user
    pressed Submit. Diagnostic only — the server's `received_at` /
    `created_at` is the authoritative timeline.
    """

    model_config = ConfigDict(extra="ignore")

    protocol_version: Literal["0.1"]
    title: str = Field(min_length=1, max_length=200)
    client_ts: str = Field(min_length=1)
    report_type: Literal["bug", "feature_request"] = "bug"
    description: str = ""
    expected_behavior: str = ""
    severity: Severity = Severity.MEDIUM
    tags: list[str] = Field(default_factory=list)
    reporter: Reporter = Field(default_factory=Reporter)
    context: BugReportContext = Field(default_factory=BugReportContext)


class BugReportStatusUpdate(BaseModel):
    """Body of `PUT /reports/{id}/status` — strict severity-style validation."""

    model_config = ConfigDict(extra="ignore")

    status: Status
    fix_commit: str = ""
    fix_description: str = ""


class LifecycleEvent(BaseModel):
    """One entry in a report's lifecycle audit log.

    Field names lock to `action / by / at` per audit IF16 — the prior-art
    template/service drift (`status / changed_by / timestamp`) is the
    cautionary tale that motivated the lock.
    """

    model_config = ConfigDict(extra="allow")

    action: str
    by: str = ""
    at: str
    fix_commit: str = ""
    fix_description: str = ""


class BugReportSummary(BaseModel):
    """Compact representation used by list views and the index.

    `module`, `github_issue_url`, and `has_screenshot` are denormalized
    onto the summary so listings render without re-reading every detail
    file or row.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    report_type: str = "bug"
    severity: str = Severity.MEDIUM.value
    status: str = Status.OPEN.value
    module: str = ""
    created_at: str
    has_screenshot: bool = True
    github_issue_url: str | None = None


class BugReportDetail(BugReportSummary):
    """Full report payload returned by detail/status endpoints.

    Extends `BugReportSummary` with everything the viewer detail panel
    needs: free-text fields, tags, the captured context, lifecycle log,
    and the dual user-agent fields (server-captured + client-reported).

    `client_reported_user_agent` mirrors `context.user_agent` but is
    surfaced at the top level so consumers and viewers do not have to
    reach into the nested context to compare them.
    """

    description: str = ""
    expected_behavior: str = ""
    tags: list[str] = Field(default_factory=list)
    reporter: Reporter = Field(default_factory=Reporter)
    context: BugReportContext = Field(default_factory=BugReportContext)
    lifecycle: list[LifecycleEvent] = Field(default_factory=list)
    server_user_agent: str = ""
    client_reported_user_agent: str = ""
    environment: str = ""
    client_ts: str = ""
    protocol_version: str = "0.1"
    updated_at: str = ""
    github_issue_number: int | None = None


class BugReportListResponse(BaseModel):
    """Pagination envelope for `GET /reports`."""

    model_config = ConfigDict(extra="ignore")

    items: list[BugReportSummary]
    total: int
    page: int = 1
    page_size: int = 20
