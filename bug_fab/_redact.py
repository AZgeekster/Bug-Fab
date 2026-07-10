"""Opt-in PII redaction for the auto-captured context buffers.

When ``Settings.redact_pii`` is true, intake passes the metadata dict
through :func:`redact_report` *before* persistence. The function
walks a known set of string fields and masks three common PII shapes:

* **JWTs** (``aaa.bbb.ccc`` triplets of base64url chars) — the two
  payload segments are replaced with ``…`` so the surrounding error
  message still reads. JWTs leak through fetch hooks when
  ``Authorization: Bearer …`` headers get logged in failing requests.
* **Credit cards** (13–19 digits with optional spaces / dashes,
  validated via Luhn) — masked to ``****-****-****-NNNN`` so the
  last four digits survive for chargeback correlation but the PAN
  itself is gone. Luhn-validating the candidate cuts the false-
  positive rate (random 16-digit transaction IDs aren't matched).
* **Emails** — the local part is replaced with ``redacted`` so
  ``alice@example.com`` becomes ``redacted@example.com``. Keeping
  the domain helps consumers diagnose without surfacing the user.

Fields scanned (only ones a user-facing UI can populate with PII):

- top-level ``description`` and ``expected_behavior`` (free text)
- ``title`` (free text, usually short)
- ``context.console_errors[*].message`` (captured exceptions can
  carry tokens/emails that surfaced in error messages)
- ``context.console_errors[*].stack`` (same risk)
- ``context.network_log[*].url`` (query-string tokens, basic-auth in
  URL)

Fields NOT scanned (by design):

- ``reporter.{name,email,user_id}`` — the consumer's UI deliberately
  collected these; redacting silently would be misleading. Consumers
  who don't want this captured should not pass it.
- ``tags`` — short, opaque, low PII probability.
- IDs / timestamps / enum values — no PII payload.

The redactor is conservative: false-positive masking is preferred
over leaking PII. Consumers who need stricter or looser rules can
subclass and override or post-process the dict before persistence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

#: Mask used in place of redacted JWT payloads. Kept short so error
#: messages remain readable around the redaction.
_JWT_MASK = "<redacted-jwt>"

#: Replacement for the local part of an email. The ``@`` and the
#: domain are preserved so consumers can still tell things like
#: "this came from a corporate domain" without seeing the user.
_EMAIL_LOCAL_MASK = "redacted"

#: Three base64url-ish segments separated by dots. We don't try to
#: validate the JOSE header — anything matching the shape is treated
#: as a JWT-shaped token. Length floors are conservative to avoid
#: matching short identifier triplets.
_JWT_RE = re.compile(r"\b[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")

#: Sequence of 13–19 digits with optional space or dash separators
#: between groups of 4. Validated via Luhn before we mask so phone
#: numbers, order IDs, and timestamps don't get caught.
_CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

#: Standard pragmatic email regex — RFC 5322 lite. Doesn't pretend
#: to catch every edge case in the spec, just the shapes a UI would
#: realistically capture from a paste or error log.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _luhn_ok(digits: str) -> bool:
    """Return True if ``digits`` (all ASCII digit chars) passes Luhn."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        n = ord(ch) - 48
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _mask_card(match: re.Match[str]) -> str:
    """Mask a credit-card candidate, keeping the last four digits."""
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not (13 <= len(digits) <= 19) or not _luhn_ok(digits):
        # Not a real card — leave the original token alone.
        return raw
    last4 = digits[-4:]
    masked = ("*" * (len(digits) - 4)) + last4
    # Restore grouping in fours for readability: ****-****-****-NNNN
    chunks = [masked[i : i + 4] for i in range(0, len(masked), 4)]
    return "-".join(chunks)


def _mask_email(match: re.Match[str]) -> str:
    """Replace the local part of an email; keep ``@`` + domain intact."""
    local, _, domain = match.group(0).partition("@")
    if not domain:
        return match.group(0)
    return f"{_EMAIL_LOCAL_MASK}@{domain}"


def redact_text(text: str) -> str:
    """Apply all redaction passes to a single string.

    Order matters: JWT before email, because the dots in an email
    domain (``a@b.example.com``) could otherwise be a partial JWT
    match. Card masking comes last because it operates on the
    surviving digit groups.
    """
    if not text:
        return text
    text = _JWT_RE.sub(_JWT_MASK, text)
    text = _EMAIL_RE.sub(_mask_email, text)
    text = _CARD_CANDIDATE_RE.sub(_mask_card, text)
    return text


def _redact_list_of_dicts(
    items: Any,
    string_fields: tuple[str, ...],
) -> list[Any]:
    """Walk a list of dicts and redact named string fields in each."""
    if not isinstance(items, list):
        return items
    out: list[Any] = []
    for item in items:
        if isinstance(item, Mapping):
            new_item = dict(item)
            for field in string_fields:
                val = new_item.get(field)
                if isinstance(val, str):
                    new_item[field] = redact_text(val)
            out.append(new_item)
        else:
            out.append(item)
    return out


def redact_report(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted shallow copy of ``metadata``.

    Pure (does not mutate the input). Fields not named in the scan
    list are preserved as-is. A field absent from the input remains
    absent in the output — this function does NOT add keys.
    """
    out: dict[str, Any] = dict(metadata)
    for top_field in ("title", "description", "expected_behavior"):
        val = out.get(top_field)
        if isinstance(val, str):
            out[top_field] = redact_text(val)
    context_raw = out.get("context")
    if isinstance(context_raw, Mapping):
        context = dict(context_raw)
        context["console_errors"] = _redact_list_of_dicts(
            context.get("console_errors"), ("message", "stack")
        )
        context["network_log"] = _redact_list_of_dicts(context.get("network_log"), ("url",))
        out["context"] = context
    return out


def safe_url(url: str) -> str:
    """Reduce a URL to ``scheme://host`` so it is safe to write to a log.

    Outbound webhook URLs are routinely *themselves* the credential —
    ``hooks.slack.com/services/T…/B…/<secret>``,
    ``discord.com/api/webhooks/<id>/<token>``. Anyone holding the full URL
    can post as the integration. Logging one at WARN on every delivery
    failure copies it into whatever log sink the consumer runs, where it
    outlives the incident and is rarely treated as secret material.

    The host is retained because it is the only part an operator needs in
    order to tell *which* integration failed. Userinfo, path, query, and
    fragment are dropped — the secret can hide in any of them.

    Never raises: this runs inside exception handlers, and a redactor that
    throws while logging an error would replace the real failure with its own.
    """
    try:
        parsed = urlsplit(url)
    except ValueError:  # pragma: no cover - urlsplit is near-total
        return "<unparseable-url>"
    if not parsed.scheme or not parsed.hostname:
        return "<unparseable-url>"
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


__all__ = ["redact_report", "redact_text", "safe_url"]
