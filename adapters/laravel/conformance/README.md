# Laravel adapter — cross-stack conformance harness

Boots a minimal Laravel 11 application that consumes this adapter via a
Composer `path` repository, then runs the upstream Bug-Fab Python pytest
conformance suite against it from a sibling Python 3.12 container. One
command, pass/fail.

## Why this exists

The adapter's own PHPUnit suite (via orchestra/testbench) hits the router
in-process. That proves the PHP is internally consistent, but **doesn't**
prove the wire protocol is byte-for-byte compatible with the upstream
Python plugin. This harness closes that gap — it runs the same
`pytest --bug-fab-conformance` checks every other Bug-Fab adapter has to
pass.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose`, not `docker-compose`).
- No host PHP, Composer, or Python install required — everything runs in
  containers.

## Run

```bash
./run-conformance.sh
```

The script exits with the pytest container's status: `0` for a passing
suite, non-zero for failures or boot problems. On Windows, run via
Git Bash or WSL.

Equivalent direct invocation (skips the teardown trap):

```bash
docker compose -f conformance/docker-compose.yml up \
    --build \
    --abort-on-container-exit \
    --exit-code-from conformance \
    conformance laravel-adapter
```

## How it works

1. **`laravel-adapter` service** — `php:8.3-cli`. On first boot:
   - Installs `git`, `unzip`, `libsqlite3-dev`, `curl` and the
     `pdo_sqlite` + `zip` PHP extensions.
   - Installs Composer from getcomposer.org.
   - Runs `composer create-project laravel/laravel:^11.0 app` into a
     persisted named volume so subsequent runs reuse the skeleton.
   - Registers the parent directory (`../`, bind-mounted at `/adapter`)
     as a Composer `path` repository and `composer require`s
     `bugfab/laravel-adapter:*@dev`. The package's auto-discovered
     service provider does the rest.
   - Runs `php artisan migrate` against an SQLite file under the app's
     `database/` directory.
   - Exec's `php artisan serve --host=0.0.0.0 --port=8080`.
2. **Healthcheck** — Compose waits until
   `GET http://localhost:8080/api/bug-reports` returns a non-5xx
   response. A bare GET on the intake URL returns 405 Method Not Allowed
   — that counts as "server is up, just wrong verb".
3. **`conformance` service** — `python:3.12-slim`, mounts the Bug-Fab
   repo root and runs `pip install -e .` to install the local
   `bug_fab` package. Then:

   ```bash
   pytest --bug-fab-conformance \
       --base-url=http://laravel-adapter:8080/api \
       --viewer-base-url=http://laravel-adapter:8080/admin/bug-reports
   ```

   Two URLs because the Laravel adapter splits intake (open, under
   `/api/`) and viewer (admin, under `/admin/bug-reports`) — the
   documented best practice from PROTOCOL.md. The conformance plugin
   appends `/bug-reports` to the intake URL and the viewer paths to the
   viewer URL.

4. **Two consumer-side Laravel tweaks** — applied by the entrypoint
   after `composer create-project`, neither of which touches the
   adapter's `src/`:

   - **CSRF exclusion for the viewer prefix.** Laravel 11's default
     `web` middleware group includes `VerifyCsrfToken`, which 419's any
     PUT/POST coming from a remote HTTP client with no session cookie.
     The adapter mounts its viewer routes under `web` so a `sed` patch
     to `bootstrap/app.php` calls
     `$middleware->validateCsrfTokens(except: ['admin/bug-reports',
     'admin/bug-reports/*'])`. This mirrors the documented best practice
     for hosting API-shaped routes alongside a Laravel web app and is
     the same exclusion a real consumer would make.
   - **`post_max_size = 20M`.** `php artisan serve` (PHP CLI server)
     honors `php.ini`'s default `post_max_size = 8M`, which rejects the
     conformance's 11 MiB oversize-screenshot test at the server layer
     before the adapter's own 4 MiB cap can return the
     spec-mandated 413. The harness writes `zz-conformance.ini` into
     `/usr/local/etc/php/conf.d/` to bump the cap above the protocol's
     10 MiB envelope.

5. **Teardown** — `run-conformance.sh`'s `EXIT` trap calls
   `docker compose down --volumes` on the way out so the named
   `laravel_app` volume doesn't accumulate across runs. Set
   `KEEP_RUNNING=1` to skip teardown and poke the running server at
   <http://localhost:8080/admin/bug-reports/> (after adding a host port
   mapping via override).

## Files

| File                  | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `docker-compose.yml`  | Two-service stack: Laravel host + Python pytest runner        |
| `run-conformance.sh`  | One-shot wrapper with cleanup + exit-code propagation         |
| `README.md`           | This file                                                     |
| `app/` (synthesized)  | The minimal Laravel skeleton — auto-created on first boot     |

The wiring lives entirely under this directory; no adapter sources are
modified by the conformance setup.

## Troubleshooting

- **First boot is slow.** Composer downloads laravel/laravel plus the
  adapter's transitive deps (~150 packages). Subsequent runs reuse the
  `bugfab-conformance-laravel_laravel_app` volume.
- **`composer create-project` fails behind a corporate proxy.** Export
  `HTTP_PROXY` / `HTTPS_PROXY` before invoking the script; Compose
  passes them through to the container.
- **`laravel-adapter` healthcheck times out.** Tail the logs:
  `docker compose logs laravel-adapter`. Common causes: a PHP fatal
  during `php artisan migrate`, or `composer require` rejecting the
  path-repo because the adapter's `composer.json` PHP constraint can't
  be satisfied on the container.
- **Want to keep the server running after a failure?** Run with
  `KEEP_RUNNING=1 ./run-conformance.sh`, then `docker compose down`
  when you're done.

## See also

- [`bug_fab/conformance/README.md`](../../../bug_fab/conformance/README.md)
  — what the pytest plugin asserts and how it works.
- [`docs/CONFORMANCE.md`](../../../docs/CONFORMANCE.md) — upstream
  conformance methodology for all adapters.
- [`adapters/laravel/README.md`](../README.md) — adapter overview and
  in-process PHPUnit suite.
