# Bug-Fab Static Bundle

The browser-side payload of Bug-Fab. Drop it into any web frontend with a
single `<script>` tag, regardless of the backend stack.

## Files

| File | Size | Purpose |
|---|---|---|
| `bug-fab.js` | small (~30 KB unminified) | Consolidated bundle: FAB, overlay, annotation canvas, error/network buffer, module detection. ES2020+. IIFE-wrapped. |
| `bug-fab.css` | placeholder | All styles are injected by `bug-fab.js`; this file is documentation only. See its header comment. |
| `vendor/html2canvas.min.js` | ~194 KB | Pinned `html2canvas@1.4.1`. Released under MIT (Niklas von Hertzen) plus an embedded Microsoft helper notice — both license headers preserved verbatim. |

## Quick start

```html
<!-- 1. Serve the bundle from anywhere on the same origin. -->
<script src="/static/bug-fab.js" defer></script>

<!-- 2. Configure on DOMContentLoaded. -->
<script>
  document.addEventListener("DOMContentLoaded", () => {
    window.BugFab.init({
      submitUrl: "/api/bug-reports",
      headers: () => ({ "X-CSRF-Token": getCsrfToken() }),
      appVersion: "1.2.3",
      environment: "prod",
    });
  });
</script>
```

`bug-fab.js` looks for `vendor/html2canvas.min.js` as a sibling of itself
on the same origin. To override (e.g. CDN), pass `html2canvasUrl` in the
init config.

## Auto-init

By default, the bundle calls `init({})` on `DOMContentLoaded` if it has not
already been initialized. This means a single `<script>` tag with no
follow-up code renders a FAB — but you must still configure `submitUrl`
before the user clicks it, otherwise the click handler logs an error.

To disable auto-init, set `window.BugFabAutoInit = false` before the
script loads:

```html
<script>window.BugFabAutoInit = false;</script>
<script src="/static/bug-fab.js" defer></script>
```

## Public API

```js
window.BugFab = {
  init(config = {}),
  open(),         // programmatic open (captures screenshot + opens overlay)
  destroy(),      // remove FAB + overlay, restore window.fetch
  version: "0.1.0a1",
};
```

### `config`

| Key | Type | Default | Notes |
|---|---|---|---|
| `submitUrl` | `string` | `null` | **Required** before opening. POST endpoint. |
| `headers` | `object` or `() => object` | `null` | Extra request headers (CSRF, auth). |
| `enabled` | `() => boolean` | always-on | Predicate gating FAB visibility. |
| `moduleMap` | `{[prefix: string]: label}` | `null` | Pathname-prefix to label map for the report's `context.module`. Falls back to first non-empty path segment. |
| `networkUrlPattern` | `RegExp` | match-all | Which URLs the network log captures. |
| `appVersion` | `string` | `""` | Surfaced as `context.app_version`. |
| `environment` | `string` | `""` | Surfaced as `context.environment`. |
| `cooldownSeconds` | `number` | `30` | FAB disable duration after a successful submit. |
| `bufferSize` | `number` | `50` | Cap on the error + network buffers. |
| `onSubmitSuccess` | `(report) => void` | `null` | Optional callback after a successful POST. |
| `onSubmitError` | `(error) => void` | `null` | Optional callback when the POST fails. |
| `html2canvasUrl` | `string` | sibling of `bug-fab.js` | Override where to fetch html2canvas. |

## CSS isolation

All injected CSS lives under a `bug-fab-*` class prefix. The bundle does
not depend on Bootstrap, Pico, MUI, or any other framework, and does not
emit unprefixed selectors. The FAB sits at `z-index: 9998`; the overlay
at `9999`.

If you need true isolation from a host site that aggressively styles
elements by tag name (`button`, `input`, etc.), you can fork the bundle
and switch to a Shadow DOM root — see the comment at the top of
`bug-fab.css` for context on why v0.1 ships scoped-prefix instead.

## CDN vs script tag

For v0.1, ship `bug-fab.js` and `vendor/html2canvas.min.js` from the same
origin as your app. CDN distribution (jsDelivr, unpkg) lands in v0.2 once
the package is on npm. The bundle's only network dependency at runtime is
`vendor/html2canvas.min.js`, lazy-loaded on first FAB click.

## Browser support

Modern evergreen browsers only — Chromium, Firefox, Safari (last 2
versions). Uses ES2020 features (arrow functions, optional chaining,
async/await, classes, template literals). No IE.
