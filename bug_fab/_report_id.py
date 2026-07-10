"""The single canonical report-id shape guard.

The audit calls this regex "the primary path-traversal defense," and it was
defined **four times with two incompatible bodies**: the route/viewer guards
(and the Flask/Django adapters) used ``\\d{1,12}`` while the file and SQL
storage backends used ``\\d{3,}``. The two disagree at both ends:

* ``bug-1`` passes the route guard (1 digit ≤ 12) but the storage guard
  rejects it (< 3 digits) → the request reaches storage and 404s there for
  the wrong reason.
* ``bug-1234567890123`` (13 digits) passes storage (≥ 3) but the route guard
  rejects it → the two layers disagree on which ids are even well-formed.

A security guard must have exactly one definition. Minted ids are
``bug-{n:03d}`` (optionally a one-letter environment prefix), so they always
carry **at least three** digits; the ``12`` upper bound caps the input length
a traversal payload can smuggle. ``{3,12}`` is the intersection that matches
every id this project actually mints while keeping both bounds.
"""

from __future__ import annotations

import re

#: ``bug-`` then an optional single-letter environment prefix (``P``/``D``/…)
#: then 3–12 digits. Anchored at both ends — the whole string must match.
REPORT_ID_RE = re.compile(r"^bug-[A-Za-z]?\d{3,12}$")

__all__ = ["REPORT_ID_RE", "is_valid_report_id"]


def is_valid_report_id(report_id: str) -> bool:
    """Return ``True`` when ``report_id`` matches the canonical shape guard."""
    return bool(REPORT_ID_RE.match(report_id))
