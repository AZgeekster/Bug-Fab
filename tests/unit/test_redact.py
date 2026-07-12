"""Tests for the opt-in PII redactor.

Pins the contract documented in ``bug_fab._redact``:

* JWTs (three base64url-ish segments) collapse to a fixed mask.
* Credit cards that pass Luhn collapse to ``****-****-****-NNNN``;
  random 16-digit identifiers don't.
* Email local-parts collapse; the domain survives.
* The function is pure (does not mutate input).
* Only the documented field set is touched — reporter identity,
  tags, IDs, timestamps, enums are all preserved.
"""

from __future__ import annotations

import pytest

from bug_fab._redact import redact_report, redact_text, safe_url

# A real Visa test number — passes Luhn. Source: payments docs.
VALID_TEST_CARD = "4111 1111 1111 1111"
# 16 digits, doesn't pass Luhn — must be left alone.
NOT_A_CARD = "1234 5678 9012 3456"


def test_redact_jwt_in_free_text() -> None:
    """A JWT in a description is masked; the surrounding text survives."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.fakefakefakefake"
    text = f"Authorization: Bearer {jwt} returned 401"
    out = redact_text(text)
    assert jwt not in out
    assert "<redacted-jwt>" in out
    assert "401" in out  # surrounding context preserved


def test_redact_email_keeps_domain() -> None:
    out = redact_text("contact alice@example.com about this")
    assert "alice@example.com" not in out
    assert "redacted@example.com" in out


def test_redact_valid_card_passes_luhn() -> None:
    """A real-shaped card number gets masked to ****-****-****-1111."""
    out = redact_text(f"customer entered card {VALID_TEST_CARD}")
    assert VALID_TEST_CARD not in out
    assert "1111" in out  # last 4 preserved
    assert "****" in out


def test_redact_does_not_mask_non_luhn_digit_groups() -> None:
    """A random 16-digit identifier (transaction ID) stays untouched."""
    out = redact_text(f"transaction_id={NOT_A_CARD}")
    assert NOT_A_CARD in out


def test_redact_does_not_mask_short_digit_groups() -> None:
    """Phone numbers and zip codes don't trigger the card matcher."""
    out = redact_text("call 555-1234 or zip 90210")
    assert "555-1234" in out
    assert "90210" in out


def test_redact_text_empty_string_passthrough() -> None:
    assert redact_text("") == ""


def test_redact_text_none_safe() -> None:
    """Defensive: the helper shouldn't crash on falsy non-strings either.

    ``None`` short-circuits the falsy guard and comes back unchanged — the
    previous version of this test was a verbatim copy of the empty-string
    test above and never actually passed ``None``.
    """
    assert redact_text(None) is None  # type: ignore[arg-type]


def test_redact_report_is_pure_no_mutation() -> None:
    """The input dict is NOT mutated; a fresh copy is returned."""
    payload = {
        "title": "leak: alice@example.com",
        "description": "",
        "context": {"console_errors": []},
    }
    before = dict(payload)
    redact_report(payload)
    assert payload == before


def test_redact_report_scans_top_level_free_text_fields() -> None:
    """title / description / expected_behavior are all scanned."""
    out = redact_report(
        {
            "title": "alice@example.com cannot log in",
            "description": "she pasted token eyJhbGciOiJIUzI1NiJ9.body0123456789.sig0123456789",
            "expected_behavior": "card 4111 1111 1111 1111 should redact",
        }
    )
    assert "alice@example.com" not in out["title"]
    assert "<redacted-jwt>" in out["description"]
    assert "1111" in out["expected_behavior"]
    assert "****" in out["expected_behavior"]


def test_redact_report_scans_console_errors() -> None:
    """Both .message and .stack of every console_errors entry are masked."""
    out = redact_report(
        {
            "context": {
                "console_errors": [
                    {
                        "level": "error",
                        "message": "fetch failed: alice@example.com",
                        "stack": "at fetch (https://x.test?token=eyJhbGciOiJIUzI1NiJ9.body0123456789.sig0123456789)",
                    },
                ]
            }
        }
    )
    err = out["context"]["console_errors"][0]
    assert "alice@example.com" not in err["message"]
    assert "<redacted-jwt>" in err["stack"]


def test_redact_report_scans_network_log_urls() -> None:
    out = redact_report(
        {
            "context": {
                "network_log": [
                    {
                        "url": "https://api.test/me?token=eyJhbGciOiJIUzI1NiJ9.body0123456789.sig0123456789",
                        "status": 200,
                    },
                ]
            }
        }
    )
    n = out["context"]["network_log"][0]
    assert "<redacted-jwt>" in n["url"]
    assert n["status"] == 200  # non-string fields untouched


def test_redact_report_leaves_reporter_alone() -> None:
    """Reporter identity is consumer-collected — never silently masked."""
    payload = {"reporter": {"name": "Alice", "email": "alice@example.com", "user_id": "u-42"}}
    out = redact_report(payload)
    assert out["reporter"] == payload["reporter"]


def test_redact_report_leaves_ids_and_enums_alone() -> None:
    """IDs, timestamps, severity enum values must pass through untouched."""
    payload = {
        "id": "bug-001",
        "created_at": "2026-05-20T12:00:00Z",
        "severity": "critical",
        "status": "open",
        "tags": ["ui", "alice@example.com"],  # tags NOT scanned by design
    }
    out = redact_report(payload)
    assert out["id"] == "bug-001"
    assert out["created_at"] == "2026-05-20T12:00:00Z"
    assert out["severity"] == "critical"
    assert out["status"] == "open"
    assert out["tags"] == ["ui", "alice@example.com"]


def test_redact_report_no_context_safe() -> None:
    """A report missing the context block doesn't blow up the redactor."""
    out = redact_report({"title": "alice@example.com"})
    assert "alice@example.com" not in out["title"]
    assert "context" not in out


# ---------------------------------------------------------------------------
# safe_url — outbound webhook URLs are usually the credential itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Slack / Discord / Teams incoming webhooks: the path IS the secret.
        (
            "https://hooks.slack.com/services/T00000000/B00000000/PLACEHOLDER-NOT-A-REAL-TOKEN",
            "https://hooks.slack.com",
        ),
        (
            "https://discord.com/api/webhooks/123456789/tok-en_SECRET",
            "https://discord.com",
        ),
        # Query-string tokens.
        ("https://example.com/hook?token=s3cr3t", "https://example.com"),
        # Basic-auth credentials in userinfo.
        ("https://user:hunter2@example.com/hook", "https://example.com"),
        # Non-default port is operationally useful and carries no secret.
        ("http://localhost:8080/hook/abc", "http://localhost:8080"),
        # Fragments.
        ("https://example.com/hook#frag", "https://example.com"),
    ],
)
def test_safe_url_keeps_only_scheme_and_host(url, expected):
    assert safe_url(url) == expected


@pytest.mark.parametrize("url", ["", "not a url", "///", "mailto:x@example.com"])
def test_safe_url_never_raises_on_garbage(url):
    """Runs inside exception handlers — a throwing redactor would mask the real error."""
    assert safe_url(url) == "<unparseable-url>"


def test_safe_url_drops_every_secret_bearing_component():
    """One assertion that no part of a maximally-hostile URL survives."""
    hostile = "https://user:pw@hooks.slack.com:443/services/T1/B1/SECRET?token=t#f"
    out = safe_url(hostile)
    for secret in ("user", "pw", "SECRET", "token", "T1", "B1", "services", "#f"):
        assert secret not in out, f"{secret!r} leaked through safe_url"
