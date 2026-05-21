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

# Optional: pre-built static marketing site co-hosted at /. Built outside
# the Docker context and synced into marketing-dist/ before deploy. See
# the "Co-hosting a marketing site at /" section below for the workflow.
# Safe to leave the directory empty — the example app falls back to the
# playground at / when no marketing-dist/index.html is present.
COPY marketing-dist /app/marketing-dist

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
  BUG_FAB_RATE_LIMIT_MAX = "5"
  BUG_FAB_RATE_LIMIT_WINDOW_SECONDS = "900"
  BUG_FAB_MAX_UPLOAD_MB = "2"
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
  auto_stop_machines = "stop"  # machine sleeps when idle; ~$0.15/mo
  auto_start_machines = true
  min_machines_running = 0
  processes = ["app"]

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 256
```

Key choices:

- **`min_machines_running = 0`** + **`auto_stop_machines = "stop"`** lets
  the machine sleep when idle. Cost drops to roughly the volume's
  $0.15/month; trade-off is a 1-3 s cold start on the first request
  after idle. If you'd rather pay always-on for instant first-clicks,
  set `min_machines_running = 1` and `auto_stop_machines = false`.
- **`BUG_FAB_RATE_LIMIT_MAX = "5"` / `_WINDOW_SECONDS = "900"`** — five
  reports per IP per fifteen minutes. Tight on purpose for a wide-open
  public demo; see § Hardening below for the rationale and the
  internal-deployment defaults.
- **`BUG_FAB_MAX_UPLOAD_MB = "2"`** — caps the multipart screenshot at
  2 MiB. Plenty of room for html2canvas on a 4K display; no room for
  a memory-chewing oversize upload.
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

## Co-hosting a marketing site at `/`

The POC image has an optional second role: it can serve a static site at
`/` while keeping the demo at `/playground`. Same machine, same volume,
no second app.

Mechanics:

- `_resolve_marketing_dir()` (in `examples/error-playground/main.py`)
  looks for `/app/marketing-dist/index.html`. Override the path via
  `BUG_FAB_MARKETING_DIR` if you want to point somewhere else.
- When that file is present, FastAPI mounts the directory at `/` via
  `StaticFiles(html=True)`. The explicit routes — `/api/bug-reports`,
  `/admin/bug-reports/*`, `/bug-fab/static/*`, `/playground`,
  `/demo/missing`, `/demo/explode` — are registered first, so the static
  mount only catches what's left.
- When the directory is absent (e.g., local dev, or a build that skipped
  the sync), `/` falls back to serving the playground HTML directly so
  the demo never 404s.
- The `Dockerfile` ships a `COPY marketing-dist /app/marketing-dist`
  step. The expectation is that you build the static site *outside* the
  Docker context (in whatever project owns the site), then copy or sync
  the build output into `marketing-dist/` at the repo root before
  `flyctl deploy` runs.
- `marketing-dist/` is gitignored. It's a synced artifact, never
  committed.

Bring your own static site (Astro, Eleventy, Hugo, plain HTML — anything
that produces a `dist/`-style folder):

```bash
# In your static-site project, build it however you normally do.
cd ../your-static-site
npm run build           # or: eleventy, hugo, etc.

# Sync the build output into Bug-Fab's repo root before deploying.
cd ../Bug-Fab
rm -rf marketing-dist
cp -r ../your-static-site/dist marketing-dist

flyctl deploy
```

After deploy, `https://your-app.fly.dev/` serves the static site and
`https://your-app.fly.dev/playground` serves the demo. Your site is
responsible for linking to `/playground` if you want visitors to find
it.

If you don't want a marketing site, do nothing. Skip the `cp` step,
leave `marketing-dist/` absent, and `/` keeps serving the playground
the way it always did.

## Hardening for a public, anonymous demo

The default `fly.toml` shipped in this repo is tuned for the canonical
public POC at `https://bug-fab.fly.dev/` — i.e., a wide-open,
anonymous, internet-addressable instance with no auth. If that
describes your deployment too, the existing config is a reasonable
starting point. If you're standing up an internal collector behind
auth or VPN, you can leave most of these knobs at their package
defaults.

### Tightened package knobs

These are existing `bug_fab` package settings (the package defaults are
unchanged); the public POC just dials them down:

```toml
[env]
  BUG_FAB_RATE_LIMIT_ENABLED = "true"
  BUG_FAB_RATE_LIMIT_MAX = "5"
  BUG_FAB_RATE_LIMIT_WINDOW_SECONDS = "900"
  BUG_FAB_MAX_UPLOAD_MB = "2"
```

- **`BUG_FAB_RATE_LIMIT_MAX=5` per `WINDOW_SECONDS=900`** — five reports
  per IP per fifteen minutes. Nobody legitimately files five reports
  in fifteen minutes; the package default of 50/hour is sized for an
  authenticated internal deployment where the cost of a wrong rejection
  is higher than the cost of a flood.
- **`BUG_FAB_MAX_UPLOAD_MB=2`** — html2canvas at typical page sizes
  produces well under 1 MiB. 2 MiB leaves headroom for a 4K display
  without giving an attacker room to chew memory. The package default
  is 10 MiB, again sized for a forgiving internal deployment.

These knobs work for any consumer of the `bug_fab` package — there's
nothing POC-specific about them. The above values are the recommended
shape for an *anonymous public* instance.

### Playground-only abuse caps

`examples/error-playground/main.py` adds three env vars that the
`bug_fab` package itself does not ship. They're enforced inside the
example app via a `FileStorage` subclass plus an ASGI middleware:

```toml
[env]
  BUG_FAB_PLAYGROUND_MAX_REPORTS = "500"
  BUG_FAB_PLAYGROUND_MAX_DISK_MB = "200"
  BUG_FAB_PLAYGROUND_MAX_BODY_KB = "2200"
```

- **`BUG_FAB_PLAYGROUND_MAX_REPORTS`** — hard ceiling on the number of
  stored reports. After each successful save, the oldest reports
  (by `created_at`) are deleted FIFO until the count is back under
  cap. `0` (the default) disables the check.
- **`BUG_FAB_PLAYGROUND_MAX_DISK_MB`** — hard ceiling on the total bytes
  used by `bug-*.json` + `bug-*.png` on the volume. Same FIFO eviction
  as above; `0` disables.
- **`BUG_FAB_PLAYGROUND_MAX_BODY_KB`** — pre-route cap on the
  `Content-Length` of `POST /api/bug-reports`. Oversize requests are
  rejected with `413` *before* uvicorn buffers the body, so a 50 MiB
  metadata blob costs roughly nothing. `0` disables.

All three default to `0` so unit tests and local dev see no extra
restrictions. The public POC opts in via `fly.toml`.

These caps live in the example file, **not** the `bug_fab` package.
Self-hosters who want them must either keep running
`examples/error-playground/main.py` as their entry point, or copy the
`_CappedFileStorage` and `_BodySizeLimitMiddleware` classes into their
own app. Other consumers of the package don't get these caps
automatically — which is intentional, since a private internal
collector wouldn't want FIFO eviction surprising users.

### Worst-case math

A rough envelope for the values above:

- Per-IP rate limit: 5 reports / 15 min × 2 MiB/screenshot ≈ 40 MiB/hr
  of disk consumption from a single source.
- Global ceiling: 500 reports / 200 MiB. A sustained flood across many
  IPs hits the global cap, then FIFO eviction kicks in — old demo
  submissions are evicted, but the volume never fills.
- The pre-route 413 means request bodies above `MAX_BODY_KB` never get
  read into memory; the cost of a malformed flood is bounded by the
  rate limiter, not by request size.

Pick your own numbers based on your volume size and how much demo
history you want to keep. The shape above leaves comfortable headroom
on the 1 GB Fly volume from the recipe earlier in this doc.

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
