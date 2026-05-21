# Cross-stack conformance harness — Rails (Engine) adapter

This directory wires the upstream `bug-fab-conformance` pytest suite to the
Rails Engine adapter's in-repo dummy host app, so a single command verifies
that the Rails adapter honors the [Bug-Fab wire protocol](../../../docs/PROTOCOL.md)
end-to-end.

The harness is intentionally Docker-only — it runs the same way on a
maintainer's laptop, a CI runner, and a reviewer's box without any Ruby, Rails
or Python installed on the host. The conformance plugin and the adapter never
share a process; everything is HTTP, exactly as a real consumer would integrate.

## Prerequisites

- Docker 24+ with the `docker compose` v2 plugin (or the legacy `docker-compose` binary).
- Roughly 1 GB of disk for the first `ruby:3.3` + `python:3.12-slim` pull and
  the first `bundle install` (Rails 7.1, sqlite3, puma, and their dependencies).

No Ruby toolchain. No Python toolchain. No `bundle install` on the host.

## Run

From this directory:

```sh
./run-conformance.sh
```

What happens:

1. `docker compose up -d rails-adapter` starts a `ruby:3.3` container, runs
   `bundle install` against `conformance/Gemfile` (a harness-only manifest —
   see "Why a separate Gemfile" below), then `bundle exec ruby conformance_boot.rb`.
2. `conformance_boot.rb` boots the in-repo `test/dummy/` app with two runtime
   tweaks applied — NOT baked into the dummy app on disk:
   - `BugFab.configuration.storage_root` is set to a writable tmp dir
     (the dummy app's default at `test/dummy/storage/` lives on the read-only
     source mount).
   - `DATABASE_URL` points at a writable tmp sqlite3 file (overrides
     `test/dummy/config/database.yml` at the env-var level).

   Then it loads `test/dummy/db/schema.rb` to create the engine's tables, and
   hands off to Puma on `0.0.0.0:8080`.
3. A healthcheck polls `GET /bug-fab/bug-reports` until it returns a non-5xx
   response. Any 2xx/4xx is acceptable — we only care that the server bound
   the port and Rails is routing. GET on a POST-only route returns 404, which
   passes.
4. A `python:3.12-slim` runner container `pip install`s the Bug-Fab package
   from the repo root, then runs:

   ```sh
   pytest --bug-fab-conformance \
          --base-url=http://rails-adapter:8080/bug-fab \
          --viewer-base-url=http://rails-adapter:8080/bug-fab \
          --rootdir=/work
   ```

   The `--base-url` ends at the engine mount prefix (`/bug-fab` per
   `test/dummy/config/routes.rb`); the protocol-defined paths
   (`/bug-reports`, `/reports`, …) are appended by the conformance plugin.
5. The full transcript lands in `./out/conformance-results.txt`. The runner
   exits with pytest's exit code: 0 if every protocol clause is honored,
   non-zero if any failed.
6. The harness tears down both containers on success or failure.

## What passing looks like

A clean pass shows the pytest summary line near the bottom of the transcript:

```
================== 30 passed in X.XXs ==================
```

…and `run-conformance.sh` exits 0. When that is the case, update the adapter's
top-level `README.md` "Conformance status" row to record the date and the N/N
tally.

## What a real failure looks like

```
================== M failed, K passed in X.XXs ==================
```

…and the exit code is 1. Each failing test's message names the protocol clause
it asserts (e.g. *"CC12: deprecated status 'resolved' MUST be rejected on
write"*). The protocol spec is the canonical authority — do **not** patch the
conformance suite to work around an adapter bug. If the adapter is wrong, fix
the adapter; if you are sure the suite misreads the spec, open an upstream
issue with the failing transcript and a curl reproduction.

## Re-running after a code change

The Rails source is mounted read-only into the adapter container, so editing
`app/`, `lib/` or `config/routes.rb` on the host and re-running the script picks
the change up on the next boot. The runner container reinstalls the local
`bug_fab` package each run, so changes there are picked up too. No image
rebuild required.

If you change `conformance/Gemfile`, the next `bundle install` re-resolves
inside the bundle-cache volume. To force a clean re-resolve, run
`docker volume rm conformance_bundle-cache` between runs.

## Why a separate `Gemfile` and `conformance_boot.rb`?

Two reasons, both about staying inert against the engine source:

1. **The engine's `../Gemfile` does not pin Puma** — it's library code, and a
   real consumer brings its own app server. The dummy app's `bin/rails server`
   needs one at runtime. Rather than adding `gem "puma"` to `../Gemfile`
   (which would alter the engine's lockfile in every PR), the harness ships
   its own `Gemfile` that re-uses the engine's gemspec and adds `puma` +
   `sqlite3` for the dummy app.
2. **The dummy app's `Rails.root` lives on a read-only mount** — its default
   `storage/` and `db/` paths can't be written to from the container. Rather
   than editing `test/dummy/config/initializers/` or `database.yml` (both
   would count as source changes), the harness applies a `storage_root`
   override and a `DATABASE_URL` override at runtime, inside
   `conformance_boot.rb`, against tmp paths.

This means a clean clone of the repo + `./run-conformance.sh` is enough to
verify conformance — no `bundle install` on host, no manual `db:setup`, no
initializer edits.

## When to use plain `docker compose` directly

If you want to iterate without the script's teardown, you can:

```sh
docker compose -f docker-compose.yml up -d rails-adapter
docker compose -f docker-compose.yml run --rm runner
# ...iterate...
docker compose -f docker-compose.yml down --volumes
```

`run-conformance.sh` is just the documented one-shot path.

## Latest result

Last green run: **30/30** passed against `bug_fab-rails` HEAD on `ruby:3.3`
under the dummy host app at `mount BugFab::Engine => "/bug-fab"`. Re-run with
`./run-conformance.sh` and overwrite this line when the tally changes.
