# Image used by the public Bug-Fab POC at https://bug-fab.fly.dev/.
# Also a copy-pasteable starting point for any consumer following
# docs/POC_HOSTING.md. Builds a tiny Python image that runs
# examples/error-playground/main.py — the demo with intentional-error
# buttons + the Bug-Fab FAB wired to a FileStorage backend.
#
# WHY install from this checkout (not PyPI): the POC tracks `main`, so
# every push that lands here can be redeployed before any tag/release.

FROM python:3.12-slim

WORKDIR /app

# Layer the install so dependency-only changes don't bust the
# pip-cache layer.
COPY pyproject.toml README.md LICENSE /app/
COPY bug_fab /app/bug_fab
COPY static /app/static

RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir 'uvicorn[standard]' && \
    cp -r /app/static /usr/local/lib/python3.12/site-packages/bug_fab/static
# WHY the cp: the wheel's `[tool.hatch.build.targets.wheel.shared-data]`
# hook puts static under `<sys.prefix>/share/bug_fab/static/`, but the
# example apps probe `<package_root>/static` first. Copying it there lets
# `_resolve_static_dir()` succeed without modifying the example. The
# proper fix (either include static as real package_data or extend the
# probe list) is tracked as a v0.1.x follow-up.

# Example app + its little demo-error endpoints.
COPY examples /app/examples

# fly.toml mounts a 1 GB volume at /data; this env var tells the example
# app to put bug_reports/ there so submissions survive redeploys.
ENV BUG_FAB_STORAGE_DIR=/data/bug_reports

EXPOSE 8080

# Run uvicorn from the example's directory so `main:app` resolves; this
# avoids the hyphen-in-module-path pitfall (Python can't import
# `error-playground.main`).
WORKDIR /app/examples/error-playground

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
