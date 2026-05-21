// svelte.config.js for the conformance SvelteKit app.
//
// adapter-node produces a self-contained Node server in ./build that we
// launch with `node build`. This is the canonical production-mode boot
// for SvelteKit apps targeting Node — matches what consumers ship.
//
// The canonical consumer routes from `../examples/route-tree/src/` are
// copied into `./src/routes/` and `./src/lib/` by the docker-compose
// boot step. SvelteKit's `files.routes` config supports out-of-project
// paths but `vite build` then complains about the route files being
// outside the Vite root and silently produces no route entries — copying
// them in keeps it well within the trodden path.
import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      // adapter-node's defaults: out: 'build', precompress: false,
      // envPrefix: '' (so PORT, HOST work without prefix).
    }),
    files: {
      appTemplate: 'src/app.html'
    },
    // CSRF — the intake endpoint accepts cross-origin multipart from the
    // conformance container. Disable checkOrigin globally so the Python
    // tester (on a different hostname) can POST to /api/bug-reports.
    // Real consumers keep this on; see ../README.md § "CSRF trade-off".
    csrf: {
      checkOrigin: false
    }
  }
};

export default config;
