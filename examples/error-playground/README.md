# `error-playground/` — Bug-Fab POC

A FastAPI app meant for **public hosting** as a demo of Bug-Fab. The
sibling `fastapi-minimal/` is the smallest-possible integration; this one
adds eight buttons that intentionally trigger errors so visitors have
something to break before clicking the bug icon.

This is what `docs/POC_HOSTING.md` deploys to Fly.io.

## What you get

- The same Bug-Fab wiring as `fastapi-minimal/` (intake + viewer + static
  bundle).
- A landing page with a row of red buttons that each trip one of the
  bundle's capture paths (`window.onerror`, `console.error`,
  `unhandledrejection`, `fetch`).
- Two intentionally-broken endpoints (`/demo/missing` → 404,
  `/demo/explode` → 500) for the failing-fetch buttons to hit.
- Storage location read from `BUG_FAB_STORAGE_DIR` so the same image
  works locally, on a Fly.io volume, or on a Hugging Face Space.

## Run locally

```bash
cd examples/error-playground
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e "../.."
pip install "uvicorn[standard]"
python main.py
# -> http://127.0.0.1:8000/
# -> http://127.0.0.1:8000/admin/bug-reports/
```

## Deploy

See `docs/POC_HOSTING.md` for the Fly.io recipe (Dockerfile + fly.toml +
volume). The Dockerfile launches `examples.error-playground.main:app`.

## What lives in `BUG_FAB_STORAGE_DIR`

- `bug-NNN.json` per submitted report
- `bug-NNN.png` per submission's screenshot
- `index.json` listing all known reports for fast viewer pagination
- `archive/` — closed reports moved off the active list

## Don't paste secrets

The viewer is wide open by default. Anyone who opens the public URL can
see every report. The POC is a demo, not an ops tool. Real deployments
should put the viewer behind auth — see
`docs/DEPLOYMENT_OPTIONS.md` § Router mount-point auth pattern.
