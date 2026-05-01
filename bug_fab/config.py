"""Runtime configuration for the Bug-Fab adapter.

A plain `dataclass`-based settings object is used (not pydantic-settings) to
keep the optional-dependency surface small — `pydantic` itself is already a
hard dep, but `pydantic-settings` is not, and adding it would make this
module pull from a separate package.

All env vars are `BUG_FAB_*`-prefixed. The module exposes a single
`Settings.from_env()` factory plus a `default_viewer_permissions()` helper.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var with permissive truthy/falsy literals."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse an integer env var, falling back to the default on missing/invalid."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    """Read a string env var, treating unset and empty as the default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw


def default_viewer_permissions() -> dict[str, bool]:
    """All-true viewer permissions (used both as default and as a fresh-copy source)."""
    return {"can_edit_status": True, "can_delete": True, "can_bulk": True}


@dataclass
class Settings:
    """Adapter-level configuration knobs.

    `viewer_permissions` is a dict (not flat fields) because the viewer
    router consumes it as a single mapping; consumers can spread their
    role-policy logic across whichever keys they want without a schema
    bump.
    """

    storage_dir: Path = field(default_factory=lambda: Path("./bug_reports"))
    id_prefix: str = ""
    max_upload_mb: int = 10
    rate_limit_enabled: bool = False
    rate_limit_max: int = 50
    rate_limit_window_seconds: int = 3600
    viewer_enabled: bool = True
    viewer_page_size: int = 20
    github_enabled: bool = False
    github_pat: str = ""
    github_repo: str = ""
    github_api_base: str = "https://api.github.com"
    viewer_permissions: dict[str, bool] = field(default_factory=default_viewer_permissions)
    #: Optional per-request CSP nonce provider for the viewer's inline
    #: ``<script>`` blocks. When set, the viewer router calls this with
    #: the active ``Request`` and stamps the returned string onto each
    #: inline script tag as ``nonce="..."``. Returning ``None`` (or
    #: leaving the field unset) renders the templates without a nonce
    #: attribute, preserving back-compat for consumers that have no CSP
    #: or that allow ``'unsafe-inline'``. Bug-Fab does NOT generate the
    #: nonce or set the ``Content-Security-Policy`` header itself — the
    #: nonce string MUST match the value the consumer's middleware emits
    #: in the response header on the same request. See
    #: ``docs/CSP.md`` for the full integration recipe.
    csp_nonce_provider: Callable[[Any], str | None] | None = None

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        """Build a `Settings` from `BUG_FAB_*` env vars; explicit kwargs win.

        WHY explicit kwargs win: tests and consumers embedding Bug-Fab in a
        larger config system need a deterministic override path that does
        not require unsetting global env vars.
        """
        values: dict[str, Any] = {
            "storage_dir": Path(_env_str("BUG_FAB_STORAGE_DIR", "./bug_reports")),
            "id_prefix": _env_str("BUG_FAB_ID_PREFIX", ""),
            "max_upload_mb": _env_int("BUG_FAB_MAX_UPLOAD_MB", 10),
            "rate_limit_enabled": _env_bool("BUG_FAB_RATE_LIMIT_ENABLED", False),
            "rate_limit_max": _env_int("BUG_FAB_RATE_LIMIT_MAX", 50),
            "rate_limit_window_seconds": _env_int("BUG_FAB_RATE_LIMIT_WINDOW_SECONDS", 3600),
            "viewer_enabled": _env_bool("BUG_FAB_VIEWER_ENABLED", True),
            "viewer_page_size": _env_int("BUG_FAB_VIEWER_PAGE_SIZE", 20),
            "github_enabled": _env_bool("BUG_FAB_GITHUB_ENABLED", False),
            "github_pat": _env_str("BUG_FAB_GITHUB_PAT", ""),
            "github_repo": _env_str("BUG_FAB_GITHUB_REPO", ""),
            "github_api_base": _env_str("BUG_FAB_GITHUB_API_BASE", "https://api.github.com"),
            "viewer_permissions": default_viewer_permissions(),
        }
        values.update(overrides)
        return cls(**values)
