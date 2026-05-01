# POC Hosting on Fly.io

This is the recipe used to host the public Bug-Fab POC on
[Fly.io](https://fly.io). It runs `examples/error-playground/` with a
persistent volume for screenshots, costs $0 on the free tier, and
gives anyone with a browser somewhere to click around without
installing the package locally. The error-playground variant has eight
"trigger an error" buttons and two intentionally-broken endpoints so
visitors have something to break before clicking the FAB; the sibling
`examples/fastapi-minimal/` is a smaller starting point for your own
integration.

You can use the same recipe to host a demo for your own fork, or to
stand up a centralized
[remote-collector](DEPLOYMENT_OPTIONS.md#remote-collector-pattern)
instance for embedded / browser-extension consumers.

## Why Fly.io

- **Always-on free tier** — no cold-start lag. The FAB feels
  instant.
- **Persistent volumes** — `BUG_FAB_STORAGE_DIR` survives redeploys,
  which is the whole point of a demo.
- **Free Postgres add-on** — handy if you want to demo
  `PostgresStorage` alongside the file backend.
- **Small image, fast deploys** — a Python wheel + the static bundle
  is ~10 MB.

The trade-off: Fly.io requires a credit card on signup (no charge at
free tier). If that's a non-starter, the runner-up is a Hugging Face
Docker space (no credit card, slightly less polished).

## Prerequisites

- A [Fly.io](https://fly.io) account — free tier with a credit card.
- The `flyctl` CLI:
  ```bash
  curl -L https://fly.io/install.sh | sh
  ```
- A clone of this repo, or your fork:
  ```bash
  git clone https://github.com/AZgeekster/Bug-Fab.git
  cd Bug-Fab
  ```

## 1. Initialize the Fly app

From the repo root:

```bash
flyctl auth login
flyctl launch --no-deploy --name bug-fab-poc --region sjc
```

`flyctl launch` will detect the `Dockerfile` (or generate one — see
below) and create a `fly.toml`. Pick a region close to you and your
demo audience.

## 2. Provide a `Dockerfile`

If the repo doesn't already include one (early v0.1 alpha may not),
drop this at the repo root:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install Bug-Fab from this checkout (so the demo runs the source you
# pushed, not the latest PyPI release).
COPY pyproject.toml README.md LICENSE /app/
COPY bug_fab /app/bug_fab
COPY static /app/static
COPY examples /app/examples

RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "examples.error-playground.main:app", \
     "--host", "0.0.0.0", "--port", "8080"]
```

## 3. Configure `fly.toml`

`flyctl launch` generates a starter `fly.toml`; replace its body with
something like this:

```toml
app = "bug-fab-poc"
primary_region = "sjc"

[build]

[env]
  BUG_FAB_STORAGE_DIR = "/data/bug_reports"
  BUG_FAB_RATE_LIMIT_ENABLED = "true"
  BUG_FAB_RATE_LIMIT_MAX = "50"
  BUG_FAB_RATE_LIMIT_WINDOW_SECONDS = "3600"
  BUG_FAB_VIEWER_ENABLED = "true"
  BUG_FAB_VIEWER_PAGE_SIZE = "20"
  # Leave GitHub sync OFF on the public POC so we don't accidentally
  # spam our own repo with demo bug reports.
  BUG_FAB_GITHUB_ENABLED = "false"

[mounts]
  source = "bug_fab_data"
  destination = "/data"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false   # always-on; no cold starts
  auto_start_machines = true
  min_machines_running = 1
  processes = ["app"]

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 256
```

Key choices:

- **`min_machines_running = 1`** + **`auto_stop_machines = false`** keeps
  the demo always-on. Without these, the machine sleeps after idle
  and the first FAB click takes ~3 seconds to wake.
- **`memory_mb = 256`** is enough for the FastAPI process plus
  occasional screenshot processing. Bump to 512 if you enable Postgres.
- **`cpu_kind = "shared"`** is the free-tier shape.

## 4. Create the persistent volume

```bash
flyctl volumes create bug_fab_data --region sjc --size 1
```

`--size 1` = 1 GB, which holds ~10,000 screenshots at typical sizes.
You can grow it later without losing data.

## 5. Deploy

```bash
flyctl deploy
```

First deploy takes ~2 minutes (Docker build + push). Subsequent
deploys with no Dockerfile changes are ~30 seconds.

When it finishes, `flyctl status` will print the public URL — usually
something like `https://bug-fab-poc.fly.dev`.

## 6. Verify

1. Open `https://your-app.fly.dev` in a browser.
2. The example FastAPI page renders.
3. The floating bug icon appears in the corner.
4. Click it, fill in the form, draw on the screenshot, hit submit.
5. The viewer at `https://your-app.fly.dev/admin/bug-reports` shows
   your new report.

## Updating

```bash
git pull       # pull latest from your fork
flyctl deploy  # redeploy
```

The volume's contents persist across deploys, so demo data sticks
around.

## Locking down the viewer

The example app ships with the viewer wide open so the public can poke
around. For a real centralized collector instance you'll want to put
the viewer behind auth — see [DEPLOYMENT_OPTIONS.md § Router
mount-point auth pattern](DEPLOYMENT_OPTIONS.md#router-mount-point-auth-pattern).

A common pattern for Fly.io specifically: put the viewer prefix
behind Cloudflare Access or Tailscale Funnel and leave the submit
endpoint open.

## Cost

Free tier as configured:

- 1 shared-cpu VM, 256 MB — covered by the free allowance.
- 1 GB persistent volume — covered by the free allowance.
- Outbound bandwidth — first 100 GB/month free.

The credit card on file gets charged $0/month at this shape. If you
scale up CPU, memory, or volumes, Fly's standard pricing applies.

## Plan B: Hugging Face Spaces

If you can't or won't put a credit card on Fly.io:

- Create a [Hugging Face Spaces](https://huggingface.co/spaces) Docker
  Space.
- Push the same `Dockerfile`.
- Persistent storage is more limited; SQLite + small `BUG_FAB_STORAGE_DIR`
  works fine for a demo, but expect more friction than Fly.io.
- Bonus: no credit card required.

The Bug-Fab project itself uses Fly.io for the canonical public POC;
Hugging Face Spaces is documented here as the credit-card-free
fallback for forks and self-hosters.
