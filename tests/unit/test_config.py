"""Unit tests for ``Settings.from_env`` + ``default_viewer_permissions``.

The factory is the single source of truth for adapter wiring — every
``BUG_FAB_*`` env var is read here. These tests pin the parsing behavior
(boolean truthy/falsy literals, integer fallbacks on garbage input) so a
careless regex tweak cannot silently re-introduce coercion drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bug_fab.config import Settings, default_viewer_permissions

# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------


def test_defaults_when_no_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars set → every field equals the documented default."""
    for key in [
        "BUG_FAB_STORAGE_DIR",
        "BUG_FAB_ID_PREFIX",
        "BUG_FAB_MAX_UPLOAD_MB",
        "BUG_FAB_RATE_LIMIT_ENABLED",
        "BUG_FAB_RATE_LIMIT_MAX",
        "BUG_FAB_RATE_LIMIT_WINDOW_SECONDS",
        "BUG_FAB_VIEWER_ENABLED",
        "BUG_FAB_VIEWER_PAGE_SIZE",
        "BUG_FAB_GITHUB_ENABLED",
        "BUG_FAB_GITHUB_PAT",
        "BUG_FAB_GITHUB_REPO",
        "BUG_FAB_GITHUB_API_BASE",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env()
    assert settings.storage_dir == Path("./bug_reports")
    assert settings.id_prefix == ""
    assert settings.max_upload_mb == 10
    assert settings.rate_limit_enabled is False
    assert settings.rate_limit_max == 50
    assert settings.rate_limit_window_seconds == 3600
    assert settings.viewer_enabled is True
    assert settings.viewer_page_size == 20
    assert settings.github_enabled is False
    assert settings.github_pat == ""
    assert settings.github_repo == ""
    assert settings.github_api_base == "https://api.github.com"
    assert settings.viewer_permissions == {
        "can_edit_status": True,
        "can_delete": True,
        "can_bulk": True,
    }


def test_default_viewer_permissions_independent_copies() -> None:
    """Each call returns a fresh dict so mutations don't leak across instances."""
    a = default_viewer_permissions()
    b = default_viewer_permissions()
    a["can_delete"] = False
    assert b["can_delete"] is True


# -----------------------------------------------------------------------------
# Env var parsing
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "ON", "True", "Yes"])
def test_bool_env_truthy_literals(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("BUG_FAB_RATE_LIMIT_ENABLED", raw)
    settings = Settings.from_env()
    assert settings.rate_limit_enabled is True


@pytest.mark.parametrize(
    "raw",
    ["0", "false", "FALSE", "no", "off", "anything-else", "", "  ", "2"],
)
def test_bool_env_falsy_literals(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("BUG_FAB_RATE_LIMIT_ENABLED", raw)
    settings = Settings.from_env()
    assert settings.rate_limit_enabled is False


def test_int_env_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUG_FAB_MAX_UPLOAD_MB", "not-a-number")
    settings = Settings.from_env()
    assert settings.max_upload_mb == 10


def test_int_env_empty_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUG_FAB_MAX_UPLOAD_MB", "  ")
    settings = Settings.from_env()
    assert settings.max_upload_mb == 10


def test_int_env_negative_value_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative ints parse — caller policy decides whether to reject them."""
    monkeypatch.setenv("BUG_FAB_RATE_LIMIT_MAX", "-5")
    settings = Settings.from_env()
    assert settings.rate_limit_max == -5


def test_str_env_empty_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike ints, empty string for a string env var yields the empty string."""
    monkeypatch.setenv("BUG_FAB_GITHUB_PAT", "")
    settings = Settings.from_env()
    assert settings.github_pat == ""


def test_full_env_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All env vars set → every value flows through to the dataclass."""
    monkeypatch.setenv("BUG_FAB_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("BUG_FAB_ID_PREFIX", "P")
    monkeypatch.setenv("BUG_FAB_MAX_UPLOAD_MB", "25")
    monkeypatch.setenv("BUG_FAB_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_RATE_LIMIT_MAX", "200")
    monkeypatch.setenv("BUG_FAB_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("BUG_FAB_VIEWER_ENABLED", "false")
    monkeypatch.setenv("BUG_FAB_VIEWER_PAGE_SIZE", "75")
    monkeypatch.setenv("BUG_FAB_GITHUB_ENABLED", "yes")
    monkeypatch.setenv("BUG_FAB_GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("BUG_FAB_GITHUB_REPO", "owner/repo")
    monkeypatch.setenv("BUG_FAB_GITHUB_API_BASE", "https://example.com/api/v3")

    settings = Settings.from_env()
    assert settings.storage_dir == Path(str(tmp_path / "store"))
    assert settings.id_prefix == "P"
    assert settings.max_upload_mb == 25
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_max == 200
    assert settings.rate_limit_window_seconds == 60
    assert settings.viewer_enabled is False
    assert settings.viewer_page_size == 75
    assert settings.github_enabled is True
    assert settings.github_pat == "ghp_test"
    assert settings.github_repo == "owner/repo"
    assert settings.github_api_base == "https://example.com/api/v3"


# -----------------------------------------------------------------------------
# Override precedence
# -----------------------------------------------------------------------------


def test_overrides_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_MAX_UPLOAD_MB", "5")
    settings = Settings.from_env(max_upload_mb=99)
    assert settings.max_upload_mb == 99


def test_overrides_replace_viewer_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUG_FAB_RATE_LIMIT_ENABLED", raising=False)
    settings = Settings.from_env(
        viewer_permissions={"can_edit_status": False, "can_delete": False, "can_bulk": False}
    )
    assert settings.viewer_permissions == {
        "can_edit_status": False,
        "can_delete": False,
        "can_bulk": False,
    }


# -----------------------------------------------------------------------------
# Public re-exports + lazy storage imports
# -----------------------------------------------------------------------------


def test_public_module_re_exports() -> None:
    """Top-level ``bug_fab`` exposes the documented surface."""
    import bug_fab

    expected = {
        "BugReport",
        "BugReportContext",
        "BugReportCreate",
        "BugReportDetail",
        "BugReportListResponse",
        "BugReportStatusUpdate",
        "BugReportSummary",
        "FileStorage",
        "LifecycleEvent",
        "Severity",
        "Status",
        "Storage",
        "__version__",
    }
    assert expected.issubset(set(bug_fab.__all__))
    # BugReport alias points at the richest schema
    assert bug_fab.BugReport is bug_fab.BugReportDetail
    assert isinstance(bug_fab.__version__, str)


def test_storage_lazy_import_sqlite() -> None:
    """``bug_fab.storage.SQLiteStorage`` resolves via ``__getattr__``."""
    from bug_fab import storage

    sqlite = storage.SQLiteStorage  # triggers the lazy load
    assert sqlite is not None
    assert sqlite.__name__ == "SQLiteStorage"


def test_storage_lazy_import_postgres() -> None:
    """``bug_fab.storage.PostgresStorage`` resolves via ``__getattr__``."""
    from bug_fab import storage

    pg = storage.PostgresStorage
    assert pg is not None
    assert pg.__name__ == "PostgresStorage"


def test_storage_unknown_attr_raises() -> None:
    from bug_fab import storage

    with pytest.raises(AttributeError):
        _ = storage.NonExistentStorage  # type: ignore[attr-defined]
