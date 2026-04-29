# Bug-Fab — React / SPA example

A minimal React + Vite app demonstrating how to consume the Bug-Fab vanilla
JS bundle from a Single Page Application.

The Bug-Fab frontend ships as a framework-agnostic IIFE bundle (see
[`repo/static/`](../../static/)). This example wraps that bundle in a
small React component (`<BugFabProvider>`) plus a `useBugFab()` hook so
that React-style consumers can integrate without writing any direct DOM
glue.

## What it demonstrates

- **Provider pattern.** `<BugFabProvider config={...}>` injects the bundle
  `<script>` once on mount, calls `window.BugFab.init(config)`, and tears
  everything down (including restoring the original `window.fetch`) on
  unmount.
- **`useBugFab()` hook.** Programmatic open from anywhere in the tree —
  useful for menu items, error boundaries, keyboard shortcuts, or any UI
  beyond the auto-rendered floating bug icon.
- **StrictMode safety.** Module-scoped script-load tracking prevents
  double-injection under React 18's `<StrictMode>` double-invoke. The
  bundle's own `init()` is idempotent, so the init call is also safe to
  re-issue.
- **TypeScript types.** The provider's config interface mirrors the bundle's
  config schema, so misconfigured keys fail at compile time rather than
  silently producing broken reports.

## Setup

```bash
cd examples/react-spa
npm install
npm run dev
```

Vite serves the React app at `http://localhost:5173`.

### You also need a Bug-Fab backend

This example assumes the FastAPI reference adapter from
[`examples/fastapi-minimal/`](../fastapi-minimal/) is running at
`http://localhost:8000`. The Vite dev server proxies two paths to it:

| Vite path | Proxied to |
| --- | --- |
| `POST /api/bug-reports` | `POST http://localhost:8000/api/bug-reports` (the protocol's intake endpoint) |
| `GET /bug-fab/bug-fab.js` | `GET http://localhost:8000/bug-fab/static/bug-fab.js` (the vanilla bundle) |
| `GET /bug-fab/vendor/*` | `GET http://localhost:8000/bug-fab/static/vendor/*` (`html2canvas.min.js`) |

So the typical local-dev workflow is two terminals:

```bash
# Terminal 1
cd examples/fastapi-minimal
uvicorn main:app --reload

# Terminal 2
cd examples/react-spa
npm run dev
```

Then open [http://localhost:5173](http://localhost:5173) and click the
floating bug icon.

### Alternative: serve the bundle from React's `public/`

If you would rather not proxy the bundle (e.g., your backend is in a
different language, or you want offline dev), copy `bug-fab.js` and
`vendor/html2canvas.min.js` into a `public/bug-fab/` directory inside this
example. Vite will then serve them statically — no proxy needed for the
bundle itself, only for `/api/bug-reports` (which you still need a
protocol-honoring backend to handle).

```bash
mkdir -p public/bug-fab/vendor
cp ../../static/bug-fab.js public/bug-fab/
cp ../../static/vendor/html2canvas.min.js public/bug-fab/vendor/
```

The default `bundlePath` (`/bug-fab/bug-fab.js`) resolves the same way
either way; only the proxy entry in `vite.config.ts` becomes optional.

## How the wrapper works

```tsx
import { BugFabProvider, useBugFab } from "./BugFabProvider";

function MyMenu() {
  const { open } = useBugFab();
  return <button onClick={open}>Report a bug</button>;
}

export default function App() {
  return (
    <BugFabProvider
      config={{
        submitUrl: "/api/bug-reports",
        appVersion: "1.0.0",
        environment: "dev",
      }}
    >
      <MyApp />
      <MyMenu />
    </BugFabProvider>
  );
}
```

The provider's `config` accepts every key the vanilla bundle accepts
(see [`repo/static/README.md`](../../static/README.md)) plus one
provider-only key:

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `bundlePath` | `string` | `/bug-fab/bug-fab.js` | Where the provider script-injects the bundle from. |

All other keys (`submitUrl`, `headers`, `enabled`, `moduleMap`,
`networkUrlPattern`, `appVersion`, `environment`, `cooldownSeconds`,
`bufferSize`, `onSubmitSuccess`, `onSubmitError`, `html2canvasUrl`)
forward unchanged to `window.BugFab.init()`.

## A note on npm distribution

Bug-Fab v0.1 does **not** ship as an npm package. The bundle is hosted by
the consumer — typically by their backend (the FastAPI adapter mounts it
at `/bug-fab/static/bug-fab.js`) or as a static asset copied into the
SPA's `public/` directory.

This example reflects that constraint: the provider injects a `<script>`
tag at runtime rather than importing from a package.

A first-class npm package (`@bug-fab/react`, plus a vanilla
`@bug-fab/core` matching the IIFE bundle 1:1) is on the v0.2 roadmap.
When it lands, this example will be updated to demonstrate both
approaches; the script-injection path will remain supported because it
covers consumers whose build pipeline cannot pull from npm (air-gapped,
embedded, etc.).

## Where to find the protocol contract

Anything your backend serves at `/api/bug-reports` MUST honor the wire
protocol documented in [`repo/docs/PROTOCOL.md`](../../docs/PROTOCOL.md).
That document is the source of truth — the React component and the
vanilla bundle both speak it, and any conformant backend (Python or
otherwise) will work.

For non-Python backends specifically, see
[`repo/docs/ADAPTERS.md`](../../docs/ADAPTERS.md) for reference sketches
in Express, Razor Pages, SvelteKit, and Go.
