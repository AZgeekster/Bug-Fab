#!/usr/bin/env python3
"""Pre-commit hook: scan staged file content for forbidden strings.

Bug-Fab is a public open-source project that lives downstream of several
private consumer applications. To prevent accidental leakage of private
project names, internal hostnames, or client identifiers into the public
repo, this hook reads ``.pre-commit-forbidden-strings.txt`` (one entry per
line) and refuses any commit whose staged content contains a match.

Matches are case-insensitive and whole-substring.
Lines beginning with ``#`` in the forbidden-strings file are treated as
comments and skipped. Blank lines are ignored.

Usage (invoked by pre-commit):
    python scripts/check_forbidden_strings.py FILE [FILE ...]

Exit codes:
    0 — no forbidden strings found
    1 — one or more forbidden strings found (commit blocked)
    2 — usage error (forbidden-strings file missing, etc.)
"""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_LIST_FILE = Path(".pre-commit-forbidden-strings.txt")


def load_forbidden_terms(path: Path) -> list[str]:
    """Read the forbidden-strings file and return non-comment, non-blank entries."""
    if not path.is_file():
        print(
            f"error: forbidden-strings list not found at {path}",
            file=sys.stderr,
        )
        sys.exit(2)

    terms: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms


def scan_file(file_path: Path, terms: list[str]) -> list[tuple[int, str, str]]:
    """Scan a single file for forbidden terms.

    Returns a list of (line_number, term, line_content) tuples for each match.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        # Binary or unreadable files are skipped silently.
        return []

    findings: list[tuple[int, str, str]] = []
    lower_terms = [(term, term.lower()) for term in terms]
    for line_no, line in enumerate(content.splitlines(), start=1):
        lower_line = line.lower()
        for original, lowered in lower_terms:
            if lowered in lower_line:
                findings.append((line_no, original, line.strip()))
    return findings


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        # Nothing staged — pre-commit invokes us with the file list.
        return 0

    terms = load_forbidden_terms(FORBIDDEN_LIST_FILE)
    if not terms:
        return 0

    failed = False
    for file_arg in argv[1:]:
        path = Path(file_arg)
        if not path.is_file():
            continue
        findings = scan_file(path, terms)
        if findings:
            failed = True
            print(f"\n{path}: forbidden strings detected")
            for line_no, term, snippet in findings:
                print(f"  line {line_no}: {term!r} in: {snippet}")

    if failed:
        print(
            "\nCommit blocked. Remove the flagged strings or, if a match is "
            "a false positive, narrow the forbidden term in "
            ".pre-commit-forbidden-strings.txt and re-run.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
