# FAQ

Common questions when adopting Bug-Fab. If you don't see yours here,
[open an issue](https://github.com/AZgeekster/Bug-Fab/issues) — chances
are someone else has the same one.

## Adoption

### Why no AuthAdapter in v0.1?

Because the right shape for an auth abstraction is not yet obvious.
The consumer roster Bug-Fab is designed for spans full RBAC, JWT/SSO,
family-account-style roles, and "no auth at all" hobby projects. A
v0.1 `AuthAdapter` ABC designed in a vacuum would almost certainly be
the wrong shape, get reworked in v0.2, and break consumer
integrations.

The v0.1 strategy is **mount-point delegation**: ship two routers
(`submit_router` and `viewer_router`), let consumers mount each under
URL prefixes their existing auth middleware already covers. It works
for every consumer shape, requires zero new abstractions, and buys
time to design `AuthAdapter` against real integration evidence rather
than speculation. v0.2 lands the proper ABC after a handful of v0.1
consumers reveal which methods are actually needed (`is_admin`,
`get_user_email`, `audit_view`, etc.).

See [DEPLOYMENT_OPTIONS.md § Router mount-point auth pattern](DEPLOYMENT_OPTIONS.md#router-mount-point-auth-pattern)
for the three common shapes (admin-only viewer, auth everywhere, no
auth at all).

### Can I switch from SQLite to Postgres later?

Yes. The Pydantic schemas and the `Storage` ABC are identical across
the SQL backends — same column shapes, same query interface. The
migration in v0.1 is manual:

1. Stop the app (or put it in maintenance mode).
2. Export reports from SQLite via `Storage.list_reports()` →
   `Storage.get_report(id)` for each.
3. Configure `PostgresStorage` and re-import.
4. Move the screenshot directory across (or just point
   `screenshot_dir` at the same path — it's storage-backend-agnostic).

A first-class `bug-fab migrate` script lands in v0.2. For now, the
manual path takes ~15 lines of Python and is straightforward enough
that we'd rather ship the v0.1 release than block on the helper.

### Is the viewer required?

No. Set `viewer_enabled=false` (or `BUG_FAB_VIEWER_ENABLED=false`) for
intake-only deployments — useful for centralized collectors where you
don't want a viewer surface at all, or for consumers who already have
an admin UI and just want the wire-protocol intake.

```python
from bug_fab.routers import submit as submit_module

settings = bug_fab.Settings.from_env(viewer_enabled=False)
submit_module.configure(storage=storage, settings=settings)
app.include_router(bug_fab.submit_router, prefix="/api")
# No viewer_router mount.
```

The submit endpoint is fully usable without the viewer.

### How do I disable destructive actions while keeping the read-only viewer?

Use `viewer_permissions`. Mount-point auth gates **whether the viewer
is reachable**; `viewer_permissions` gates **what destructive actions
are exposed once the viewer is reachable**. This is the right knob
when you want a manager role to view reports but not delete or close
them.

```python
settings = bug_fab.Settings.from_env(
    viewer_permissions={
        "can_edit_status": False,
        "can_delete": False,
        "can_bulk": False,
    },
)
```

Defaults are `true` for all three. The viewer pages render without the
edit/delete/bulk buttons when these are off, and the corresponding
endpoints reject the requests server-side regardless of what the UI
shows.

### How do I write an adapter for X language?

Read [PROTOCOL.md](PROTOCOL.md) for the wire spec, then
[ADAPTERS.md](ADAPTERS.md) for sketches in Razor Pages, Express,
SvelteKit, and Go. The contract is small: accept a `multipart/form-data`
POST with `metadata` (JSON string) and `screenshot` (PNG), validate
against the schema, persist, return the documented JSON response shape.

When you have a working adapter, run [CONFORMANCE.md](CONFORMANCE.md)'s
pytest plugin against it to verify protocol compliance — the plugin
exercises every documented requirement and tells you what's missing.

PRs adding a first-party adapter (vs documentation-only sketch) are
welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Schema

### Why is severity locked to 4 values?

Protocol simplicity. The locked enum
(`low | medium | high | critical`) means:

- The viewer can color-code reliably across consumers.
- The conformance suite has a finite list to test against.
- Cross-consumer dashboards (when v0.2's centralized collector docs
  land) can aggregate severity meaningfully.

Consumer-configurable severity is on the v0.2 roadmap, triggered by
the first consumer that hits real friction with the locked set
(e.g., a project that needs `urgent` distinct from `critical`, or a
single-severity workflow). v0.1 prioritizes a sharp default over
flexibility.

### Why is rate limiting per-IP not per-user?

Because v0.1 has no `AuthAdapter`, Bug-Fab cannot ask "who is this?"
when a request comes in. Per-IP is the closest available proxy and
defaults to **off** so it doesn't surprise consumers who don't expect
it.

Per-IP is bypassable via NAT, VPN, and shared offices, so treat it
as accidental-flood protection rather than user accountability. The
public POC on Fly.io enables it (50 requests / hour / IP) because
public exposure attracts curious bots; internal deployments behind
SSO usually leave it off.

Per-user rate limiting lands in v0.2 alongside `AuthAdapter`. At that
point the default flips to per-user with a generous cap, and per-IP
becomes the fallback for unauthenticated submit endpoints.

### Does Bug-Fab capture passwords / PII?

Bug-Fab captures whatever the page is currently showing or logging. In
v0.1 specifically:

- **Screenshot**: `html2canvas` snapshots the visible viewport. If a
  password is on screen in plaintext, it's in the screenshot.
- **Console buffer**: the last N `console.error` / `console.warn`
  entries (default ~50). If your app logs auth tokens to the console,
  they ride along.
- **Network buffer**: recent `fetch` and `XHR` calls (method, URL,
  status, duration). **The body is not captured.** URL query strings
  are; if you put tokens in query strings, those are captured.

What is **not** captured:

- Form values that aren't on screen.
- Request/response bodies.
- Cookies or `localStorage` (unless your code logged them to the
  console).
- HTTP-only auth cookies — the browser doesn't expose them to JS in
  the first place.

**Redaction is the consumer's responsibility in v0.1.** If your app
shows sensitive data, the right places to scrub are:

- Don't render unmasked secrets to the DOM.
- Don't log tokens via `console.error` / `console.warn`.
- Don't put tokens in URL query strings.

A built-in PII redaction policy (auto-blur of password fields, regex
scrubbing of common token shapes, configurable allow/deny lists for
console messages) is a planned post-v0.1 feature pending a threat
model review. If you have a strong opinion on the design, please
weigh in on the corresponding GitHub issue.

### Can I use Bug-Fab offline / air-gapped?

Yes. `html2canvas` is **vendored** inside the static bundle (pinned
version, MIT license preserved), so there's no CDN dependency. The
frontend makes zero third-party network calls — the only outbound
request is the configured submit URL, which can point anywhere
including a localhost or LAN-only address.

This was a deliberate design choice for factory-floor deployments,
home-network IoT, and consumers with privacy / compliance reasons to
audit every outbound byte.

### What browsers are supported?

Modern browsers — anything that ships `fetch`, `Promise`,
`async/await`, ES2020 syntax, and `html2canvas`. In practical terms:

- Chrome, Edge, Firefox, Safari — last two major versions.
- Mobile Safari and Chrome on Android — same.
- Internet Explorer is **not** supported.

The exact browser matrix is being finalized pre-v0.1; we lean
"modern only, no transpilation" so the bundle stays small.

## Operational

### Where do screenshots live?

Always on disk, regardless of metadata backend. Even with
`SQLiteStorage` or `PostgresStorage`, the PNG itself goes to the
configured `screenshot_dir` (or `BUG_FAB_STORAGE_DIR` for
`FileStorage`). This keeps DB rows small, makes screenshots servable
as static files, and gives you an obvious filesystem path for
forensic inspection or backup.

### Does the GitHub sync block submission if GitHub is down?

No. GitHub Issues sync is **best-effort by protocol**. If the GitHub
API call fails (rate limit, network blip, expired PAT), the
submission still succeeds — the response returns
`github_issue_url: null` and the failure is logged. You will never
lose a bug report because GitHub had a bad five seconds.

### Can I run multiple Bug-Fab instances behind a load balancer?

Yes for `SQLiteStorage` and `PostgresStorage` (the database
serializes the writes). For `FileStorage`, you need a shared
filesystem mounted at `BUG_FAB_STORAGE_DIR` on every replica
(NFS, EFS, etc.) — local-disk file storage with multiple replicas
will silently lose reports.

### How big is the JS bundle?

Roughly 150 KB minified including the vendored `html2canvas`. The
Bug-Fab-specific JS by itself is much smaller (~20 KB);
`html2canvas` is the heavyweight. An opt-out build for SPA
consumers who supply their own `html2canvas` is on the v0.2
roadmap.

### Why is the project called Bug-Fab?

"Bug" + "FAB" (floating action button), the visual centerpiece of
the integration. The name was claimed before deeper thought went
into it; it's stuck around because it reads OK on a resume and
doesn't conflict with anything established. If a blocking
trademark or branding issue surfaces before v0.1 final, it gets
revisited.

## Common integration gotchas

Surfaced by real consumer integrations during v0.1 development.
Each one is a one-line fix once you know it; collected here so
future integrators (human or AI) can find the answer faster than
the original consumer did.

### My FAB renders but clicking it does nothing — what's wrong?

Almost certainly a `submitUrl` mismatch. The bundle's auto-init
defaults `submitUrl` to `/api/bug-reports` (the canonical mount
documented in the Quickstart). If your submit router is mounted
under a different prefix, override via one of:

```html
<!-- Zero-JS: data-attribute on the bundle's own <script> tag -->
<script src="/bug-fab/static/bug-fab.js"
        data-submit-url="/internal/api/bug-reports" defer></script>

<!-- Or explicit init for full control -->
<script>window.BugFabAutoInit = false;</script>
<script src="/bug-fab/static/bug-fab.js" defer></script>
<script>
  window.addEventListener("DOMContentLoaded", () => {
    window.BugFab.init({ submitUrl: "/internal/api/bug-reports" });
  });
</script>
```

This was the #1 first-time-integrator footgun until v0.1.x — the
old default was `null`, which meant the FAB rendered, the user
clicked, and nothing visible happened. Now it always has a working
default.

### My viewer 500s on first load with `no such table: bug_reports`

You're using `SQLiteStorage` or `PostgresStorage` and the schema
hasn't been created yet. As of v0.1.x, `SqlStorageBase.__init__`
auto-calls `create_all()` on construction, so this should not
happen with current main. If you're pinned to an older SHA, either
upgrade or call `storage.create_all()` explicitly at startup.

For Alembic-managed migrations (column adds, etc.), still run
`alembic upgrade head` separately — auto-init never fights an
Alembic-managed schema because `create_all` skips existing tables.

### My existing chat / help FAB collides with Bug-Fab's FAB

Both are at `bottom: 24px; right: 24px` by default and stack on
top of each other. Pick one of:

```js
// Move Bug-Fab to the opposite corner
BugFab.init({ position: "bottom-left" });

// Free-form pixel offsets (e.g., stack Bug-Fab to the LEFT of your existing FAB)
BugFab.init({ position: { bottom: "24px", right: "120px" } });

// Anchor to the existing element so theme switches / resizes don't break it
BugFab.init({ stackAbove: ".chat-fab", gap: 12 });
```

The `stackAbove` form (also `stackBelow` / `stackLeft` /
`stackRight`) measures the anchor's `getBoundingClientRect()` and
re-positions on `window.resize` and via `IntersectionObserver` /
`MutationObserver` so layout reflows don't desync the FAB.

### How do I turn the FAB off on specific pages?

Three off-switches, ranked from "fully off everywhere" to "this
page only":

```html
<!-- Zero-JS, applies to whichever pages have this attribute -->
<script src="/bug-fab/static/bug-fab.js"
        data-bug-fab-disabled="true" defer></script>
```

```js
// Programmatic, runtime
BugFab.init({ enabled: false });   // never creates the FAB at all
BugFab.disable();                  // hides it (created or not)
BugFab.enable();                   // shows it again
```

The "fully off across the entire app" pattern: gate BOTH the
backend route mount AND the `<script>` tag in your base template
on a single env-var (e.g., `BUG_FAB_ENABLED=true`). One env-var
flip and Bug-Fab is fully gone — no FAB, no admin route, no
schema init.

### How do I send every report to Slack / Linear / Pushover?

Set `BUG_FAB_WEBHOOK_ENABLED=true` and `BUG_FAB_WEBHOOK_URL=...`.
The bundle POSTs the full `BugReportDetail` JSON to that URL after
every successful save. See
[`docs/DEPLOYMENT_OPTIONS.md` § Webhook delivery](DEPLOYMENT_OPTIONS.md#webhook-delivery)
for the complete recipe including custom auth headers.
