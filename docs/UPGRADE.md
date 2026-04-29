# Upgrade Guide

Migration notes for moving between Bug-Fab versions. This guide
focuses on the **breaking changes and required actions** for each
upgrade. For the full per-release change log (additions, fixes,
deprecations, security notes), see [`CHANGELOG.md`](../CHANGELOG.md)
at the repo root.

If you only need "what changed in this release," read CHANGELOG. If
you need "how do I move my deployment from version X to version Y,"
read this file.

---

## How this guide is organized

Each upgrade section answers four questions:

1. **Is anything breaking?** A clear yes or no, up front.
2. **What do I need to do?** The minimum steps to upgrade cleanly.
3. **Optional follow-ups.** Things you don't have to do, but probably
   want to.
4. **Pitfalls.** Anything that has bitten consumers in practice.

The protocol version is independent of the package version. Most
package upgrades do not bump the protocol — when they do, it is
called out at the top of the section.

---

## From `0.1.0a1` to `0.1.0`

**Breaking?** No. `0.1.0a1` was an alpha release used to reserve the
PyPI name and validate the publish workflow; the package surface
itself was intentionally minimal. The full v0.1 feature set lands in
`0.1.0` final.

**What you need to do:**

```bash
pip install -U bug-fab    # drops the --pre flag from the alpha install
```

That's it. There is no protocol change, no config rename, no router
signature change. If you somehow already had `0.1.0a1` integrated,
the upgrade is a single `pip install -U`.

**Optional follow-ups:**

- Read [`CHANGELOG.md`](../CHANGELOG.md) for the list of features
  that landed in `0.1.0` final (most of v0.1's surface area, as
  promised in the alpha release notes).
- Wire up the GitHub Actions Trusted Publishing flow if you are
  running your own fork — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

**Pitfalls:** none expected. The alpha shipped almost nothing
runnable; this upgrade is administrative.

---

## From `0.1.x` to `0.2.0`

> This section is a forward-looking placeholder. The exact contents
> firm up once `0.2.0` is in the release-candidate phase and real
> consumer integrations have driven the final shape of the
> `AuthAdapter` ABC. Read it as "here's what's likely to change" not
> "here's what definitely will."

**Breaking?** Likely **no for the wire protocol** (additive changes
only, per the [protocol versioning rules](PROTOCOL.md#versioning)
and the [deprecated-values rule](PROTOCOL.md#deprecated-values-rule-critical)),
and **mostly no for the Python API** (new classes added; existing
mount-point auth pattern continues to work unchanged).

The two areas where consumers may want to migrate:

### `AuthAdapter` plug-point

v0.2 introduces an `AuthAdapter` ABC — the proper auth abstraction
that v0.1 deferred. Consumers are not required to migrate; the v0.1
mount-point delegation pattern continues to work. Migrate to
`AuthAdapter` if you want any of:

- **Per-user rate limiting** instead of per-IP.
- **Submitter identity displayed in the viewer** (today the viewer
  cannot show "who reported this" unless you enrich the metadata
  payload yourself).
- **Audit-on-view** logging when an admin reads a report.
- **Per-user permissions** rather than the v0.1 endpoint-level gating
  via `viewer_permissions`.

A `viewer_auth: callable` plug-point also lands in v0.2 as the
lightweight escape hatch — pass a callable that takes the request
and returns whether the viewer is reachable, useful for consumers who
want a slightly richer hook than mount-point delegation but do not
need full `AuthAdapter` features.

The v0.2 release will ship a step-by-step migration recipe for both
escape hatches.

### Storage backend migrations

v0.1's "switch from `FileStorage` to `SQLiteStorage`" path is manual
(see [FAQ § Can I switch from SQLite to Postgres later?](FAQ.md#can-i-switch-from-sqlite-to-postgres-later)).
v0.2 is expected to add a first-class `bug-fab migrate-storage` CLI
that handles the export → re-import → screenshot directory copy in a
single command.

If you have already done the manual migration on v0.1.x, you do not
need to redo it under the v0.2 CLI — the on-disk format is
unchanged.

### Protocol additions

v0.2 may add new optional fields to the wire protocol (e.g.,
fields driven by `AuthAdapter` integrations). Per the [protocol
versioning rules](PROTOCOL.md#versioning) those are **additive and
non-breaking** — v0.1 clients submitting against a v0.2 adapter
continue to work without changes. Stored reports keep recording the
`protocol_version` they were submitted under.

If a v0.2 release tightens an enum or removes a field, that bumps
the protocol version (`"0.2"`) and ships with a deprecation window
documented in the v0.2 section of CHANGELOG and a dedicated entry
here.

---

## General upgrade principles

These rules apply to every Bug-Fab upgrade and explain why most
release-to-release moves are low-risk.

### Wire protocol changes are additive by default

Per [`PROTOCOL.md` § Versioning](PROTOCOL.md#versioning):

- Adding optional fields, optional endpoints, or new error codes does
  **not** bump the protocol version. Older clients keep working
  against newer adapters.
- Renaming or removing fields, changing required-vs-optional, or
  tightening enum values **does** bump the protocol version and
  ships with a deprecation window.

Practically, this means a Bug-Fab consumer can almost always upgrade
the package version without touching the frontend bundle or the
intake payload shape.

### Deprecated values stay legal on read forever

Per [`PROTOCOL.md` § Deprecated-values rule](PROTOCOL.md#deprecated-values-rule-critical):

> Adapters MUST accept deprecated enum values on read indefinitely.
> Adapters MAY reject deprecated values on write.

If a future release retires an enum value (say, the `investigating`
status), reports already stored under the old value remain readable
and renderable. You may have to update tooling that creates new
reports with that value, but historical data is never locked away.

This is the rule that lets long-lived deployments survive several
protocol revisions without a forced re-import.

### Read CHANGELOG between versions

When you skip a release (e.g., upgrading from `0.1.0` straight to
`0.1.5`), read every intermediate `[v0.1.X]` entry in CHANGELOG. The
upgrade guide collapses several patch releases into a single
section; the per-release notes catch deprecations and security
advisories that may need attention even on otherwise no-action
upgrades.

### Pin in production

While Bug-Fab is on `0.x`, pin the exact version in your dependency
file (`bug-fab==0.1.0`, not `bug-fab>=0.1.0`). Per the semver pre-1.0
convention, minor versions may include breaking changes and you want
to be the one who decides when those land.

After `1.0.0`, normal semver caret ranges (`^1.0.0`) are safe.

### Run the conformance suite after upgrading

If you maintain a non-Python adapter, run the [conformance
plugin](CONFORMANCE.md) against your adapter after every Bug-Fab
upgrade. The conformance suite is the cheapest way to confirm a
release did not regress anything you depend on.

---

## Reporting an upgrade issue

If a documented upgrade does not go smoothly:

1. Confirm you read the corresponding section above and the
   per-release CHANGELOG entries.
2. [File an issue](https://github.com/AZgeekster/Bug-Fab/issues)
   with: source version, target version, framework, the exact error
   you saw, and a minimal reproduction if possible.
3. If the issue looks security-sensitive, file a private advisory
   instead — see [`SECURITY.md`](../SECURITY.md).

Upgrade-path bugs are high-priority because they block adoption of
fixes and features. They will not silently linger in the backlog.
