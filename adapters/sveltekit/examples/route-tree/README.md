# Example: SvelteKit consumer route tree

This directory shows the file layout a consumer drops into their existing
SvelteKit app's `src/routes/` tree to wire up Bug-Fab.

## Layout

```
src/
├── lib/server/
│   └── bug-fab.ts                          # Shared adapter wiring
└── routes/
    ├── api/
    │   └── bug-reports/+server.ts          # POST /api/bug-reports — intake (open)
    └── admin/
        └── reports/
            ├── +server.ts                  # GET /admin/reports — list (JSON)
            ├── [id]/
            │   ├── +server.ts              # GET / DELETE one
            │   ├── status/+server.ts       # PUT status
            │   └── screenshot/+server.ts   # GET PNG
            ├── bulk-close-fixed/+server.ts # POST
            └── bulk-archive-closed/+server.ts # POST
```

## Auth (mount-point delegation)

Bug-Fab v0.1 has no auth abstraction. Use SvelteKit's `hooks.server.ts` plus
`+layout.server.ts` to gate `/admin/**` behind your existing auth, while
leaving `/api/bug-reports` open. See repo/docs/PROTOCOL.md § "Auth — mount-point
delegation".

```ts
// src/hooks.server.ts (sketch)
import type { Handle } from '@sveltejs/kit';

export const handle: Handle = async ({ event, resolve }) => {
  if (event.url.pathname.startsWith('/admin')) {
    const session = event.cookies.get('session');
    if (!session || !isAdmin(session)) {
      return new Response('Forbidden', { status: 403 });
    }
    event.locals.user = decodeUser(session);
  }
  return resolve(event);
};
```

## Mounting the static bundle

The Bug-Fab browser bundle (`bug-fab.js`) is published as a static asset and
must be reachable from the consumer's pages. Two recipes:

### Option A — copy at build time (recommended)

```js
// vite.config.ts
import { sveltekit } from '@sveltejs/kit/vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';

export default {
  plugins: [
    sveltekit(),
    viteStaticCopy({
      targets: [
        {
          src: 'node_modules/bug-fab-sveltekit/dist/static/bug-fab.js',
          dest: 'bug-fab'
        }
      ]
    })
  ]
};
```

### Option B — manually copy into `static/`

Run once after install:

```sh
cp node_modules/bug-fab-sveltekit/dist/static/bug-fab.js static/bug-fab/bug-fab.js
```

SvelteKit serves files from `static/` at the root, so the URL becomes
`/bug-fab/bug-fab.js` — the default `bundleSrc` of the `<BugFab />`
component.

## Mount the FAB on every page

```svelte
<!-- src/routes/+layout.svelte -->
<script lang="ts">
  import BugFab from 'bug-fab-sveltekit/client';
  import { page } from '$app/stores';
</script>

<slot />

<BugFab
  intakeEndpoint="/api/bug-reports"
  module={$page.route.id ?? undefined}
  appVersion="1.0.0"
  environment={import.meta.env.MODE}
/>
```
