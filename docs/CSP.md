# Content-Security-Policy integration

Bug-Fab's viewer (`list.html` + `detail.html`) ships with a small
amount of inline JavaScript: a toast/copy helper in `_base.html`, a
detail-page status-form handler, and the list page's stat-card / row
/ bulk / reload wiring. These are inline by design — the viewer is
self-contained and does not require a separate `/static/viewer.js`
mount on the host app.

That self-contained shape is incompatible with a strict CSP that
forbids `'unsafe-inline'` for `script-src`. To bridge the two, the
viewer accepts a per-request **CSP nonce** through a configurable
provider callable. When set, every inline `<script>` block in the
viewer renders with a matching `nonce="..."` attribute; without it,
the templates render exactly as before (full back-compat).

This page is the integration recipe. Bug-Fab does **not** generate
the nonce, does **not** emit the `Content-Security-Policy` header,
and does **not** depend on any specific CSP middleware package. All
of that lives in the consumer's framework — the package only
*consumes* a nonce string per request.

## Why a callable, not a config string

The nonce on the `<script>` tag MUST equal the nonce in the response
header (`Content-Security-Policy: script-src 'nonce-XYZ'`) on the
same response. That value has to be generated per request and made
available to both the template render and the header writer. The
canonical pattern is:

1. Middleware generates a fresh nonce per request and stores it on
   `request.state` (or an equivalent context).
2. The same middleware sets the response header containing that
   nonce.
3. Bug-Fab reads the nonce off the request via the configured
   provider callable when rendering its templates.

Because the header is the consumer's responsibility, the provider is
the seam: the consumer wires whichever middleware they prefer, and
Bug-Fab only needs the read-side accessor.

## FastAPI: 15-line middleware example

```python
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import Response

from bug_fab.config import Settings
from bug_fab.routers.submit import configure
from bug_fab.routers.viewer import viewer_router

app = FastAPI()


@app.middleware("http")
async def csp_nonce_middleware(request: Request, call_next):
    request.state.csp_nonce = secrets.token_urlsafe(16)
    response: Response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{request.state.csp_nonce}'; "
        f"img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    )
    return response


configure(Settings(
    storage_dir="./bug_reports",
    csp_nonce_provider=lambda req: getattr(req.state, "csp_nonce", None),
))
app.include_router(viewer_router, prefix="/admin/bug-reports")
```

A few notes on the snippet:

- `secrets.token_urlsafe(16)` is 22 base64url characters, which fits
  CSP nonce requirements (the spec demands a base64-encoded value
  with at least 128 bits of entropy).
- `getattr(req.state, "csp_nonce", None)` returns `None` when the
  middleware did not run for some reason — for example a route
  outside the middleware's scope. Returning `None` makes Bug-Fab fall
  back to rendering without the nonce attribute, which is the same
  behavior as having no provider at all.
- `style-src 'unsafe-inline'` stays in the header because the viewer
  templates currently use a single inline `<style>` block in
  `_base.html`. A first-class style-nonce escape hatch is a roadmap
  item; until it lands, either keep `'unsafe-inline'` for `style-src`
  or fork the template to externalize the styles.

## Other frameworks

The same pattern translates to any framework whose request object
exposes a per-request mutable scratch space:

- **Flask:** stash the nonce on `flask.g` in a `before_request` hook
  and pass `csp_nonce_provider=lambda req: g.csp_nonce` (Bug-Fab's
  Flask example wires the request through directly; consult
  `examples/flask-minimal/` for the request-shaping detail).
- **Starlette (without FastAPI):** identical to the FastAPI sketch,
  minus the `@app.middleware("http")` shorthand — implement
  `BaseHTTPMiddleware` directly.
- **Express / Razor / SvelteKit consumers** that talk to Bug-Fab
  through the wire protocol render their own pages, not Bug-Fab's
  Jinja templates, so the nonce concern lives entirely on their
  side.

## What about the inline `<style>` block?

The viewer's `_base.html` includes one inline `<style>` block for
its scoped CSS (`.bug-fab-root *`). That block still requires
`style-src 'unsafe-inline'` (or a separate style-nonce mechanism
that Bug-Fab does not yet ship). Stamping the same nonce on
`<style>` is on the v0.2 candidate list; until then, treat
`script-src` and `style-src` as independent decisions in your CSP
header. The `bug-fab.js` FAB bundle has its own related concern —
see `docs/DEPLOYMENT_OPTIONS.md` § "Strict CSP without
'unsafe-inline'" for the runtime-injected `<style>` tag the bundle
ships and the planned config hook.

## Testing your wiring

A quick integration check from the consumer's test suite:

```python
def test_viewer_html_carries_csp_nonce(client):
    response = client.get("/admin/bug-reports/")
    assert response.status_code == 200
    assert 'nonce="' in response.text
    csp = response.headers["content-security-policy"]
    nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
    assert f'nonce="{nonce}"' in response.text
```

If the assertion fails, the most common causes are: the middleware
did not run for the viewer route (mount-prefix mismatch), the
provider lambda raised (Bug-Fab silently falls back to no-nonce on a
provider exception — check the `bug_fab` logger for
`bug_fab_csp_nonce_provider_failed`), or the response header is
being overwritten by a downstream middleware.
