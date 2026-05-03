# Bug-Fab — FastAPI + Jinja2 + Docker Example

A richer reference than `examples/fastapi-minimal/` — Jinja2 templates
rendering full HTML pages, `SQLiteStorage` for queryable metadata, and
a `Dockerfile` + `docker-compose.yml` so `docker compose up` is the
only command between clone and a running service.

## What's in here

| File | What it does |
|---|---|
| `main.py` | FastAPI app, `SQLiteStorage`, `submit.configure(...)`, viewer mount, static-bundle mount |
| `templates/base.html` | Shared layout — Bug-Fab `<script>` tag lives here so every page extending the base gets the FAB |
| `templates/home.html` | Stand-in home page extending `base.html` |
| `requirements.txt` | Bug-Fab from GitHub (pinned `<sha>`) + `uvicorn[standard]` + `jinja2` |
| `Dockerfile` | Multi-stage Python 3.12-slim build; final image carries only resolved deps |
| `docker-compose.yml` | One service, one volume mount (`./data:/data`), env-var knobs |

## Run with Docker

```bash
git clone https://github.com/AZgeekster/Bug-Fab.git
cd Bug-Fab/examples/fastapi-jinja-docker

# Pin Bug-Fab to a current commit on main:
sha=$(curl -s https://api.github.com/repos/AZgeekster/Bug-Fab/commits/main | python -c "import sys,json; print(json.load(sys.stdin)['sha'][:12])")
sed -i.bak "s/<sha>/$sha/" requirements.txt

docker compose up --build
```

Open http://localhost:8000/. Click the bug icon in the bottom-right,
draw on the screenshot, type a title, submit. The report lands in
`./data/bug-fab.db` (SQLite) with the screenshot at
`./data/screenshots/<id>.png`. Browse submitted reports at
http://localhost:8000/admin/bug-reports.

`./data/` is bind-mounted into the container, so reports survive
`docker compose down && up`.

## Run without Docker

```bash
cd examples/fastapi-jinja-docker
sed -i.bak "s/<sha>/$(git rev-parse HEAD)/" requirements.txt
pip install -r requirements.txt
uvicorn main:app --reload
```

## Customizing

- **Replace `<sha>` in `requirements.txt`** with a 7+ character commit
  SHA from <https://github.com/AZgeekster/Bug-Fab/commits/main>. The
  one-liner under "Run with Docker" automates this.
- **Auth on the viewer:** see
  [`docs/DEPLOYMENT_OPTIONS.md`](../../docs/DEPLOYMENT_OPTIONS.md) §
  "Auth recipes" — copy-paste the FastAPI HTTP-Basic snippet, add
  `BUGS_ADMIN_PASS` to `docker-compose.yml`, wrap the viewer mount in
  a `dependencies=[Depends(require_admin)]` APIRouter.
- **Rate limiting / GitHub sync:** uncomment the env-var blocks in
  `docker-compose.yml` and provide values.
- **Migrations between Bug-Fab versions:** see
  [`docs/DEPLOYMENT_OPTIONS.md`](../../docs/DEPLOYMENT_OPTIONS.md) §
  "Upgrading between Bug-Fab versions" for the Alembic recipe. Run
  the `alembic upgrade head` one-liner against `./data/bug-fab.db`
  before restarting after a Bug-Fab upgrade with schema changes.

## Going to production

This example is reference shape, not a turn-key production deploy.
The big-ticket items missing for a real deployment:

- Auth on the viewer (see "Customizing" above).
- A reverse proxy (Caddy, nginx, Traefik) terminating TLS in front of
  the container.
- Per-IP rate limiting on the intake (env vars are commented in
  `docker-compose.yml`).
- A backup story for `./data/` — `tar` it on a schedule.
- A CSP header on the host app (see `docs/DEPLOYMENT_OPTIONS.md` §
  "Content Security Policy").

The Bug-Fab side of all of these is documented in
[`docs/DEPLOYMENT_OPTIONS.md`](../../docs/DEPLOYMENT_OPTIONS.md).
