# Security Policy

Bug-Fab is a personal open-source project maintained by one person in
their spare time. Security reports are still very welcome — this page
explains how to file one and what to expect back.

## Reporting a vulnerability

**Channel:** open a private security advisory on GitHub.

- https://github.com/AZgeekster/Bug-Fab/security/advisories/new

GitHub's private vulnerability advisories let us discuss a fix in
private before any details are made public, request a CVE if the issue
warrants one, and publish a coordinated disclosure when a patched
release is ready. There is no separate email contact — the advisory
form is the one supported channel.

When you file a report, the most useful things to include are:

- Affected version (e.g., `0.1.0`, commit SHA, or "main as of
  YYYY-MM-DD").
- A short description of the issue and the impact you observed.
- Steps to reproduce, including any minimal config or payload.
- Whether the issue is already public (e.g., disclosed in another
  advisory, posted on a forum, etc.).

Please **do not** open a regular GitHub Issue for a security report —
that puts the details in public before a fix is available.

## Response expectations

Bug-Fab is **best-effort, hobbyist OSS**. The targets below are what
the maintainer aims for, not a contractual SLA:

| Stage | Target |
|---|---|
| Acknowledge the report | Within 7 days |
| Assess severity and confirm/deny | Within 14 days |
| Ship a fix for high or critical severity | Within 30 days |
| Ship a fix for low severity | Best effort; may be folded into the next regular release |

If you have heard nothing after 14 days, a polite nudge on the
advisory thread is welcome — the maintainer probably missed the
notification.

Coordinated disclosure is preferred: please give the maintainer a
reasonable window to publish a fix before going public. If you are
working to a disclosure deadline (e.g., a 90-day clock), say so in
your initial report so it can be planned around.

## Supported versions

Only the latest released version receives security fixes. Once
`v0.1.0` ships, the table below will track which lines are still
in scope.

| Version | Status | Security fixes |
|---|---|---|
| `0.1.0a1` | Alpha — not for production | No |

`0.1.0a1` exists to reserve the PyPI name and validate the publish
workflow. Do not deploy it. Track [`v0.1.0`](https://github.com/AZgeekster/Bug-Fab/milestones)
for the first supported release.

## Threat model summary (v0.1)

Bug-Fab v0.1 is a small surface: a multipart intake endpoint, a JSON
viewer, a vanilla-JS frontend, and three storage backends. The
sections below describe what the package **does** protect against and
what it **does not** — both matter for deciding how to deploy it.

### What v0.1 does protect against

- **Schema validation on intake.** `POST /bug-reports` rejects
  malformed multipart, missing required parts, wrong types, and
  unknown enum values. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for
  the full schema.
- **Strict severity and status enums.** Adapters MUST reject unknown
  values with `422`. Silent coercion fails conformance.
- **Magic-byte PNG check on the screenshot part.** A request that
  claims `image/png` but does not start with the PNG magic bytes is
  rejected — the screenshot is not blindly written to disk.
- **Atomic storage writes.** `FileStorage` writes via tmp + rename so
  a crash mid-write cannot leave a half-written `metadata.json` or
  `screenshot.png` for the viewer to render.
- **Server-captured `User-Agent` as the source of truth.** The
  request-header `User-Agent` is captured independently of the
  client-supplied `client_reported_user_agent`. The client value is
  preserved separately for diagnostics but is never trusted as
  authoritative. See [`PROTOCOL.md` §
  User-Agent trust boundary](docs/PROTOCOL.md#user-agent-trust-boundary).
- **Best-effort GitHub sync.** A GitHub outage cannot fail an
  otherwise-valid bug submission — sync errors log server-side and
  return `github_issue_url: null`.

### What v0.1 does NOT protect against

These are the deliberate limits of v0.1, not bugs. Each is a known
gap that consumers must address themselves until the corresponding
roadmap item lands. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

- **Authentication.** Bug-Fab v0.1 ships no auth abstraction. Both
  intake and viewer routes are unauthenticated by default; consumers
  protect them by mounting each router behind their existing
  framework auth middleware. The `AuthAdapter` ABC lands in v0.2.
- **Authorization / per-user permissions.** The `viewer_permissions`
  config gates which **endpoints** are mounted, not which **users**
  may call them. Per-user gating arrives with `AuthAdapter` in v0.2.
- **Per-user rate limiting.** Only per-IP rate limiting is available
  in v0.1 (off by default). Per-IP is bypassable via NAT, VPN, and
  shared offices — treat it as accidental-flood protection rather
  than user accountability.
- **Automatic PII redaction in the error / network buffers.** The
  buffers carry whatever the consumer's app emits to `console.error`,
  `console.warn`, and `fetch` / `XHR` URLs. Bug-Fab does not scrub
  them.
- **Automatic password / token redaction in screenshots.** The
  screenshot is what `html2canvas` captures from the live DOM — if a
  password field is visible, it is in the PNG.
- **Encryption at rest.** Stored screenshots and metadata sit on the
  consumer's filesystem (or in their database) in plaintext. Disk-
  level encryption is the consumer's responsibility.
- **Audit logging beyond the lifecycle field.** The `lifecycle` array
  on each report records status changes, but there is no separate
  audit log for views, downloads, or list queries. Audit-on-view
  arrives with `AuthAdapter` in v0.2.
- **Browser session protection beyond what the consumer's framework
  already provides.** Bug-Fab does not set CSRF tokens, configure
  cookies, or enforce origin checks — those belong to the host app.

### Known security considerations consumers should handle

These are things every Bug-Fab deployment needs to think about
explicitly. The package will not protect you from any of them.

- **The error and network buffers can contain whatever your app
  emits.** Auth tokens in URL query strings show up in the network
  log. Logged session secrets show up in the console buffer. The
  right place to fix this is in your app — do not log tokens, do not
  put them in query strings, do not render them to the DOM.
- **Screenshots can contain anything visible in the browser.** A user
  with a password manager open in another tab is fine; a user with a
  reset-token URL on screen is not. There is no "blur sensitive
  fields" pass in v0.1.
- **The viewer endpoints expose every submitted report to anyone with
  access to the mounted URL prefix.** Auth gating is the consumer's
  job. The most common mistake is mounting the viewer at a public
  URL because mount-point auth was never added. See
  [`docs/DEPLOYMENT_OPTIONS.md` § Router mount-point auth pattern](docs/DEPLOYMENT_OPTIONS.md#router-mount-point-auth-pattern).
- **Screenshot files are not web-public by default**, but they
  *become* exposed if you serve the storage directory as static
  content. Don't.
- **The submit endpoint is unauthenticated by default.** If your
  consumer mounts it on the public internet without rate limiting,
  enable per-IP rate limiting (`BUG_FAB_RATE_LIMIT_ENABLED=true`).
  The public POC on Fly.io runs with rate limiting on for exactly
  this reason.
- **GitHub Personal Access Tokens used for issue sync are sensitive.**
  They live in environment variables. Rotate on any incident.

### Cryptographic dependencies

Bug-Fab v0.1 has **no direct cryptographic dependencies**. Transport
security relies on the consumer's TLS configuration — Bug-Fab speaks
plaintext HTTP and the consumer's framework / reverse proxy is
expected to terminate TLS in any non-localhost deployment.

The vendored `html2canvas.min.js` is pinned at v1.4.1 inside
`static/vendor/`. If upstream `html2canvas` ships a security patch,
that pin should be rotated and a patch release of Bug-Fab cut. The
maintainer watches the `html2canvas` repo for releases; if you spot
a relevant CVE first, please file a security advisory via the
channel above.

## Once you have reported

The maintainer will:

1. Acknowledge receipt of the report.
2. Confirm whether the issue is in scope and reproducible.
3. Discuss severity, impact, and a fix plan on the advisory thread.
4. Ship a patched release.
5. Publish the advisory (with credit to the reporter, unless you
   prefer to remain anonymous) once consumers have had a reasonable
   window to upgrade.

Thank you for taking the time to report responsibly.
