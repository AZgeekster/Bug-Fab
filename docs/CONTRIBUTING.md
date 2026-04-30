# Contributing

Thanks for thinking about contributing. Bug-Fab is a personal /
hobbyist open-source project that the maintainer also happens to use
at the day job, which means:

- **Issues and PRs are very welcome** — every adopter sharpens the
  design.
- **Response time is best-effort.** Most of the time it's quick;
  occasionally it isn't. If something has been quiet for two weeks,
  feel free to bump the thread.
- **Be kind.** No harassment, no personal attacks, no off-topic
  noise. That's the whole code of conduct.

## Quick path

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/YOUR-USERNAME/Bug-Fab.git
cd Bug-Fab

# 2. Create a virtualenv and install with dev extras
python -m venv .venv
source .venv/Scripts/activate     # Git Bash on Windows
# source .venv/bin/activate       # macOS / Linux
pip install -e ".[dev]"

# 3. Install the pre-commit hooks (forbidden-strings, ruff lint, format)
pre-commit install

# 4. Make a branch, do your work, write tests
git checkout -b my-feature

# 5. Run the local verification loop
ruff check .
ruff format .
pytest

# 6. Commit (the pre-commit hook re-runs ruff and the forbidden-strings check)
git commit -am "Describe what you changed"
git push -u origin my-feature

# 7. Open a PR against AZgeekster/Bug-Fab main
```

CI runs on every push and PR — see [Continuous integration](#continuous-integration)
below for the matrix and gates.

## What to work on

If you don't have a specific itch:

- Browse [open issues](https://github.com/AZgeekster/Bug-Fab/issues),
  especially anything tagged `good first issue` or `help wanted`.
- Look at [ROADMAP.md](ROADMAP.md) — the v0.2+ candidates each
  describe what would unlock the feature. Pick one and propose an
  approach in an issue before writing the code.
- Try [POC_HOSTING.md](POC_HOSTING.md) on your own infra — friction
  you hit there is a real doc bug.

For larger changes, **please open an issue first** to align on the
shape before investing significant time. The protocol and scope are
deliberately tight; surprise PRs that expand either tend to need
significant rework.

## Local development

### Layout

```
bug_fab/                  # Python package
├── __init__.py           # public re-exports
├── config.py             # Settings dataclass + env-var loader
├── schemas.py            # Pydantic v2 models = wire protocol
├── storage/              # FileStorage + SQLiteStorage + PostgresStorage
├── integrations/         # github.py — opt-in Issues sync
└── conformance/          # pytest plugin for adapter authors

static/                   # Frontend bundle (vanilla JS + vendored html2canvas)

docs/                     # Public docs (this folder)
examples/                 # fastapi-minimal, flask-minimal, react-spa
tests/                    # Unit + integration + conformance tests
```

### Running tests

```bash
pytest                                    # full suite
pytest tests/test_storage_files.py        # one file
pytest --cov=bug_fab --cov-report=term-missing   # with coverage report
pytest -m integration                     # only integration tests
pytest -m conformance                     # only conformance tests
```

The coverage gate is **85% minimum on `bug_fab/`** (100% on the
protocol-validation layer, which is exempt from the global gate). PRs
that drop coverage below the gate will fail CI.

### Running the example app

```bash
uvicorn examples.fastapi-minimal.main:app --reload
# Open http://localhost:8000 and click the FAB
```

This is the fastest way to manually verify a frontend or wire-protocol
change end-to-end.

### Lint and format

```bash
ruff check .          # lint
ruff format .         # format
ruff format --check . # check without rewriting (CI mode)
```

Ruff replaces black, isort, flake8, and pyupgrade — one tool, fast.
Configured in `pyproject.toml`.

## Pre-commit hooks

A `.pre-commit-config.yaml` ships in the repo with three hooks:

- **forbidden-strings** — refuses commits containing private project
  names, internal infrastructure references, etc. (Generated from a
  list maintained outside the public repo. The hook keeps the public
  repo clean even when contributors port code from internal forks.)
- **ruff check** — lint.
- **ruff format --check** — format check.

After cloning, run `pre-commit install` once. The hooks then run
automatically on `git commit`. You can run them manually on the full
tree any time:

```bash
pre-commit run --all-files
```

If the forbidden-strings hook flags your commit, double-check what
you're staging — usually it's a lifted comment or a sample log line
that snuck through.

## Continuous integration

Every push and PR runs:

- **ruff lint** (`ruff check .`)
- **ruff format check** (`ruff format --check .`)
- **pytest matrix** on Python **3.10**, **3.11**, **3.12**.
- **Coverage gate** at 85% — failures block merge.
- **Schema drift check** (`python scripts/generate_protocol_schema.py --check`) — fails if the on-disk `docs/protocol-schema.json` no longer matches what the Pydantic models would generate. If you change `bug_fab/schemas.py`, regenerate the schema with `python scripts/generate_protocol_schema.py` (no flags) and commit the updated `docs/protocol-schema.json`.

On tagged releases (`v*`), CI additionally builds the wheel + sdist
and publishes to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(no long-lived tokens stored in GitHub).

## Maintainer release flow

This section is for whoever cuts a release tag. The flow is documented here
so it's reproducible across maintainers and so future-you doesn't re-trip
the bugs we already hit.

### Pre-tag gates (mandatory)

1. **Clean-venv install verification.** Spin a fresh Python 3.13 venv
   *outside* the repo and confirm a fresh install works end-to-end:
   ```
   py -3.13 -m venv "$TEMP/bug-fab-clean-venv"
   "$TEMP/bug-fab-clean-venv/Scripts/python.exe" -m pip install -e ".[dev,sqlite,postgres]"
   "$TEMP/bug-fab-clean-venv/Scripts/python.exe" -m pytest -q
   ```
   The dev environment carries hidden state from sibling projects, so
   `pip install -e .` succeeding in your dev shell is *not* sufficient.
   The clean-venv gate has caught three publish-blocking bugs across
   v0.1's lifecycle: missing `jinja2` dep, real email leaked into the
   PyPI `authors` block, and a Starlette 1.0 API break that made the
   viewer non-functional. Don't skip it. (See
   [`docs/audits/2026-04-28_clean_venv_verification_audit.md`](audits/2026-04-28_clean_venv_verification_audit.md)
   for the full case history.)

2. **Schema drift check.** `python scripts/generate_protocol_schema.py --check`
   must exit 0. If it doesn't, regenerate the schema, run the suite again,
   commit the new schema, and re-tag from the new commit.

3. **CI green on the tag's parent commit.** Don't tag if `main` is red —
   the publish job will fail and the version number on PyPI may be wasted
   (PyPI versions are immutable; even a yanked release reserves the slot).

### Pre-flight: gh CLI account check

Bug-Fab is published to `AZgeekster/Bug-Fab`. If your gh keyring holds
multiple GitHub accounts, the *active* account silently switches between
sessions. Before any push from a release branch, confirm:

```
gh auth status | head -2
```

shows `AZgeekster` as the active account. If not:

```
gh auth switch -h github.com -u AZgeekster
```

The token must have `workflow` scope. If `gh auth status` shows only
`gist`, `read:org`, `repo`, push will fail with `refusing to allow an
OAuth App to create or update workflow … without 'workflow' scope`.
Refresh with:

```
gh auth refresh -h github.com -s workflow
```

The refresh opens a browser for device-flow approval. If no browser
auto-opens, the terminal still prints a one-time code and the URL to
paste it into manually.

### Tagging and publishing

After all pre-tag gates pass and CI is green:

1. Decide the version. Alphas pin the name on PyPI without becoming the
   default install: `v0.1.0a1`, `v0.1.0a2`. Betas: `v0.1.0b1`. Final:
   `v0.1.0`.
2. Tag annotated:
   ```
   git tag -a v0.1.0aN -m "Release v0.1.0aN"
   ```
3. Push the tag:
   ```
   git push origin v0.1.0aN
   ```
4. Watch the workflow at
   `https://github.com/AZgeekster/Bug-Fab/actions/workflows/ci.yml`.
   The publish job runs only on `v*` tags and uses the `pypi`
   environment. First-ever publish requires the project's
   [Trusted Publishing](https://pypi.org/manage/account/publishing/)
   configuration on pypi.org (one-time setup).

### Post-publish

1. Verify the release at `https://pypi.org/project/bug-fab/`.
2. Test install from a fresh venv: `pip install --pre bug-fab` (alphas)
   or `pip install bug-fab` (final).
3. Update `CHANGELOG.md` if the release has post-tag notes.
4. Announce in the release-notes thread of choice (none in v0.1; revisit
   when the project has more reach).

## Adapter PRs

If you're contributing a non-Python adapter (Razor, Express,
SvelteKit, Go, etc.), there are a couple of extra expectations:

1. **Run the conformance suite** against your adapter and include the
   passing report in the PR. See [CONFORMANCE.md](CONFORMANCE.md).
2. **Honor the wire protocol exactly** — same field names, same error
   codes, same response shape. The frontend bundle does not negotiate;
   it sends what [PROTOCOL.md](PROTOCOL.md) says it sends.
3. **Don't add required fields to your adapter** that aren't in the
   spec — the frontend won't send them and the conformance tests will
   fail.
4. **Document the install/integration path** in your adapter's
   own README, mirroring the structure of `examples/fastapi-minimal/`.

Adapter sketches in [ADAPTERS.md](ADAPTERS.md) are documentation-only;
graduating one to a first-party adapter (with code, tests, and CI)
is the kind of contribution that earns a release-note shoutout.

## Commit message style

No strict format, but please:

- Use the imperative mood ("Add SQLite storage", not "Added SQLite
  storage").
- Keep the subject under 70 characters.
- Wrap the body at 72 characters.
- Reference the issue if there is one (`Closes #42`).

The maintainer's commits skew toward `Conventional Commits` style
(`feat:`, `fix:`, `docs:`, `chore:`) but it's not enforced by tooling.

## Licensing

Bug-Fab is **MIT-licensed** — see [LICENSE](../LICENSE). By
contributing, you agree your contributions are MIT-licensed under the
same terms. There is no separate CLA to sign.

If you're contributing code that you didn't write yourself, please
make sure it's also MIT-compatible and call out the source in your
PR description.

## Code of conduct

Be kind. No harassment, no personal attacks, no off-topic noise.
Disagreements about technical direction are fine and expected;
disagreements about people aren't. The maintainer will moderate
threads that drift, and reserves the right to lock or remove
content that crosses the line.

That's it. Welcome aboard.
