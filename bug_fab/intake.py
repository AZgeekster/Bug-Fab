"""Framework-agnostic intake validation for the Bug-Fab v0.1 wire protocol.

Non-FastAPI adapters (Flask, Hono, Django, NestJS, etc.) need to perform the
exact same validation pipeline that the FastAPI reference adapter in
:mod:`bug_fab.routers.submit` performs before persistence: PNG magic-byte
sniffing, screenshot size cap enforcement, content-type enforcement, JSON
parsing + Pydantic validation, and User-Agent capture from request headers.
This module factors that pipeline into a single :func:`validate_payload`
function that takes plain bytes and strings — no framework primitives — and
either returns a :class:`ValidatedPayload` or raises a typed
:class:`IntakeError` subclass that adapters map to their HTTP envelope.

The FastAPI router in :mod:`bug_fab.routers.submit` is the reference caller
and the source of truth for the validation order; this module mirrors it so
adapters that depend on :mod:`bug_fab.intake` stay protocol-conformant by
construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .schemas import BugReportCreate

#: PNG magic bytes per RFC 2083. The first eight bytes of every valid PNG
#: file MUST equal this signature; adapters reject anything else with 415
#: per ``docs/PROTOCOL.md`` § Intake.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class IntakeError(Exception):
    """Base class for protocol intake validation failures.

    Adapters catch this (or one of its subclasses) and map
    :attr:`status_code`, :attr:`code`, and :attr:`message` to whatever HTTP
    error envelope their framework emits. ``status_code`` is the HTTP
    status the protocol assigns to the failure mode; ``code`` is the
    machine-readable token from ``docs/PROTOCOL.md`` § Errors; ``message``
    is the human-readable explanation.
    """

    status_code: int = 400
    code: str = "validation_error"
    message: str = ""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class PayloadTooLarge(IntakeError):
    """Screenshot exceeds the configured size cap. Maps to HTTP 413."""

    status_code = 413
    code = "payload_too_large"
    message = "Screenshot exceeds the maximum allowed size"


class UnsupportedMediaType(IntakeError):
    """Screenshot is not a PNG (by content-type or magic bytes). Maps to HTTP 415."""

    status_code = 415
    code = "unsupported_media_type"
    message = "Screenshot must be a PNG image"


class ValidationError(IntakeError):
    """Metadata is unparseable JSON or fails Pydantic schema validation. Maps to HTTP 422.

    :attr:`detail` carries the raw Pydantic ``e.errors()`` list so adapters
    can surface field-level diagnostics in the same shape FastAPI's default
    422 envelope uses.
    """

    status_code = 422
    code = "schema_error"
    message = "Metadata failed schema validation"

    def __init__(self, message: str = "", detail: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.detail: list[dict[str, Any]] = detail if detail is not None else []


@dataclass(frozen=True)
class ValidatedPayload:
    """The validated, ready-to-persist intake payload.

    ``user_agent`` is the *server-captured* request-header value — the
    source of truth per ``docs/PROTOCOL.md`` § User-Agent semantics. The
    client-reported user agent (if any) lives on
    ``metadata.context.user_agent`` and is preserved separately for
    diagnostics.
    """

    metadata: BugReportCreate
    screenshot_bytes: bytes
    user_agent: str


def _parse_metadata(metadata_json: str) -> BugReportCreate:
    """Parse the JSON metadata string and validate it against the schema.

    Raises :class:`ValidationError` for both unparseable JSON and Pydantic
    schema violations. JSON-decode failures carry an empty ``detail`` so
    adapters can branch on a non-empty list when they want to surface
    field-level diagnostics; Pydantic failures carry the full
    ``e.errors()`` list.
    """
    try:
        metadata_obj: Any = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            message=f"metadata is not valid JSON: {exc.msg}",
            detail=[],
        ) from exc
    try:
        return BugReportCreate.model_validate(metadata_obj)
    except PydanticValidationError as exc:
        raise ValidationError(
            message="metadata failed schema validation",
            detail=list(exc.errors()),
        ) from exc


def validate_payload(
    *,
    metadata_json: str,
    screenshot_bytes: bytes,
    screenshot_content_type: str | None,
    request_user_agent: str | None,
    max_screenshot_bytes: int = 5 * 1024 * 1024,
) -> ValidatedPayload:
    """Validate a multipart bug-report submission per the v0.1 protocol.

    The validation order matches the FastAPI reference router and is
    intentional: cheap byte-level checks first (size, content-type, magic
    bytes) so we reject obvious garbage before paying for JSON parsing
    and Pydantic validation.

    Parameters
    ----------
    metadata_json:
        Raw JSON string from the multipart ``metadata`` form field.
    screenshot_bytes:
        Raw image bytes from the multipart ``screenshot`` file field.
    screenshot_content_type:
        The ``Content-Type`` of the screenshot upload, as reported by the
        multipart parser. ``None`` is treated as missing.
    request_user_agent:
        The request's ``User-Agent`` header value. ``None`` is normalized
        to ``""`` on the returned payload — the adapter never has to
        special-case absence.
    max_screenshot_bytes:
        Hard upper bound on the screenshot size. Defaults to 5 MiB per
        ``docs/PROTOCOL.md`` § Size limits.

    Returns
    -------
    ValidatedPayload
        Validated metadata, raw screenshot bytes, and server-captured UA.

    Raises
    ------
    PayloadTooLarge
        Screenshot exceeds ``max_screenshot_bytes`` (HTTP 413).
    UnsupportedMediaType
        Content-type is not ``image/png`` or magic bytes do not match
        (HTTP 415).
    ValidationError
        Metadata JSON is unparseable or fails the schema (HTTP 422).
    """
    # 1. Size check first — it's the cheapest test and rejects DoS-shaped
    #    uploads before we look at any bytes.
    if len(screenshot_bytes) > max_screenshot_bytes:
        raise PayloadTooLarge(f"Screenshot exceeds maximum size of {max_screenshot_bytes} bytes")

    # 2. Content-type enforcement. The protocol locks v0.1 to PNG; adapters
    #    that want JPEG support layer it on top after a protocol bump.
    if screenshot_content_type != "image/png":
        raise UnsupportedMediaType(
            f"Screenshot content-type must be image/png, got {screenshot_content_type!r}"
        )

    # 3. Magic-byte sniff. A consumer that sets the right Content-Type but
    #    sends non-PNG bytes (e.g. a renamed JPEG) is still rejected here.
    if not screenshot_bytes.startswith(_PNG_MAGIC):
        raise UnsupportedMediaType("Screenshot bytes do not start with the PNG magic signature")

    # 4. Metadata parse + schema validation. Bubbled-up failures carry the
    #    Pydantic error list so adapters can echo it in their 422 envelope.
    metadata = _parse_metadata(metadata_json)

    # 5. User-Agent capture. ``None`` is normalized to ``""`` so the
    #    returned payload has a single shape adapters can store directly.
    user_agent = request_user_agent if request_user_agent is not None else ""

    return ValidatedPayload(
        metadata=metadata,
        screenshot_bytes=screenshot_bytes,
        user_agent=user_agent,
    )
