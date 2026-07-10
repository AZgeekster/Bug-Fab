"""The report-id shape guard must be one definition shared by every layer.

It was defined four times with two incompatible bodies — the route/viewer
guards used ``\\d{1,12}`` and the storage backends used ``\\d{3,}`` — so a
request could pass one layer and be rejected by the next for the wrong reason.
These tests pin the single canonical definition and prove every layer now
imports the same compiled regex, not merely an equal-looking copy.
"""

from __future__ import annotations

import pytest

from bug_fab._report_id import REPORT_ID_RE, is_valid_report_id


@pytest.mark.parametrize(
    "report_id",
    [
        "bug-001",  # the minted zero-padded form
        "bug-123",
        "bug-P001",  # single-letter environment prefix
        "bug-999999999999",  # 12 digits — the upper bound
    ],
)
def test_canonical_accepts_well_formed_ids(report_id: str) -> None:
    assert is_valid_report_id(report_id)


@pytest.mark.parametrize(
    "report_id",
    [
        "bug-1",  # 1 digit: passed the old route guard, rejected by storage
        "bug-12",  # 2 digits: same cross-layer disagreement
        "bug-1234567890123",  # 13 digits: passed old storage, rejected by route
        "bug-",
        "bug-abc",
        "bug-PP001",  # two-letter prefix
        "../../etc/passwd",
        "bug-001/../bug-002",
        "bug-001.png",
    ],
)
def test_canonical_rejects_malformed_ids(report_id: str) -> None:
    assert not is_valid_report_id(report_id)


def test_every_layer_shares_one_object() -> None:
    """Not just equal patterns — the *same* compiled regex, so they cannot drift.

    Importing the module-level name from each layer and asserting identity is
    what makes a future re-introduction of a local ``re.compile`` fail loudly.
    """
    from bug_fab.adapters.flask.blueprint import _REPORT_ID_RE as flask_re
    from bug_fab.routers.viewer import _REPORT_ID_RE as viewer_re
    from bug_fab.storage._sql_base import _REPORT_ID_RE as sql_re
    from bug_fab.storage.files import _REPORT_ID_RE as files_re

    assert viewer_re is REPORT_ID_RE
    assert files_re is REPORT_ID_RE
    assert sql_re is REPORT_ID_RE
    assert flask_re is REPORT_ID_RE


def test_django_layer_shares_one_object() -> None:
    """Django's `REPORT_ID_RE` is the canonical object too.

    Split out because importing the Django storage module touches
    `django.conf.settings`, which the rest of the suite configures only in the
    Django adapter tests. Skip cleanly when Django is unconfigured.
    """
    pytest.importorskip("django")
    try:
        from bug_fab.adapters.django.storage import REPORT_ID_RE as django_re
    except Exception as exc:  # ImproperlyConfigured when settings absent
        pytest.skip(f"Django not configured in this process: {exc}")
    assert django_re is REPORT_ID_RE
