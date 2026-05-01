# Changelog

All notable changes to Bug-Fab are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While Bug-Fab is on `0.x`, minor version bumps may include breaking
changes per the semver pre-1.0 convention. Breaking changes are called
out explicitly in each release entry.

## [Unreleased]

### Added

- `Settings.csp_nonce_provider` callable hook — when set, the viewer
  stamps the returned per-request nonce onto every inline `<script>`
  tag in `list.html`, `detail.html`, and `_base.html`. Lets consumers
  adopt a strict `Content-Security-Policy` (no `'unsafe-inline'` for
  `script-src`) without forking the package. See
  [`docs/CSP.md`](docs/CSP.md) for the FastAPI middleware recipe.
  Default is `None`, preserving existing rendering behavior.

### Changed

- Replaced the inline `onclick="window.location.reload()"` on the
  list-view Refresh button with a `data-bug-fab-action="reload"`
  attribute and a single `addEventListener` registration in the
  page's existing `<script>` block. Strict CSP forbids inline event
  handlers; this keeps the same UX while letting the page render
  cleanly under `script-src 'nonce-...'` without `'unsafe-inline'`.
- Tightened the FastAPI intake router's image-format validation to match
  PROTOCOL.md v0.1: `POST /bug-reports` now accepts only `image/png`
  screenshots and rejects JPEG (and every other format) with `415
  Unsupported Media Type`. The bundled `html2canvas` client only emits
  PNG, the protocol-validation library `bug_fab.intake` already enforced
  PNG-only, and the viewer's `GET /reports/{id}/screenshot` always
  returned `image/png`; the router was the lone outlier and silently
  accepted JPEG bytes that were then served back with the wrong
  Content-Type. Not a breaking protocol change — the spec and JSON
  Schema have always been PNG-only.

### Deprecated

### Removed

### Fixed

- Resolved the drift between the intake router (which accepted both PNG
  and JPEG) and the viewer screenshot endpoint (which always served
  `image/png`). Stored screenshots are now guaranteed to match the
  served Content-Type because intake rejects non-PNG bytes by magic
  signature.

### Security

- Document the CSP-nonce integration path
  ([`docs/CSP.md`](docs/CSP.md)) so consumers running strict CSP have
  a first-class hook into the viewer's inline scripts instead of
  needing to whitelist `'unsafe-inline'` or fork templates.

## [0.1.0a1] - 2026-04-27

Initial alpha release. Reserves the `bug-fab` name on PyPI and validates
the publish workflow end-to-end before the `v0.1.0` final release.

`pip install bug-fab` skips alphas by default; install with
`pip install --pre bug-fab` to try this version.

### Added

- Project scaffolding: `pyproject.toml` (PEP 621, Hatchling backend),
  ruff lint and format configuration, pytest configuration with
  coverage gating at 85%.
- Optional dependency extras: `bug-fab[sqlite]` and `bug-fab[postgres]`
  for SQL storage backends via SQLAlchemy and Alembic.
- Pytest plugin entry-point `bug-fab-conformance` reserved for the
  protocol conformance suite consumed by adapter authors.
- Pre-commit configuration with forbidden-strings, ruff, and standard
  hygiene hooks.
- Editor and git metadata: `.editorconfig`, `.gitattributes` enforcing
  LF line endings and treating vendored JS as binary.
- GitHub Actions CI: matrix testing on Python 3.10 / 3.11 / 3.12,
  ruff lint and format checks, coverage gate, wheel and sdist build
  with `twine check`, Trusted Publishing to PyPI on `v*` tags.

### Notes

- This release exists primarily to claim the `bug-fab` PyPI name and
  exercise the build/publish pipeline. The package surface itself is
  intentionally minimal; the full v0.1 feature set lands in `0.1.0`.
- Wire-protocol contract is not yet locked. Do not build production
  integrations against this alpha.

[Unreleased]: https://github.com/AZgeekster/Bug-Fab/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/AZgeekster/Bug-Fab/releases/tag/v0.1.0a1
