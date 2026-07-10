// svelte.config.js for the bug-fab-sveltekit package itself.
// Consumers ship their own svelte.config.js; this one only affects packaging
// of the bundled <BugFab /> Svelte component.
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // No kit adapter is configured — this is a library, not an app.
  }
  // `@sveltejs/package` v2 removed the `package: { source, dir, emitTypes }`
  // config key (it errored with "config.package is no longer supported").
  // Those settings are now CLI flags on `svelte-package` — see the build
  // script in package.json (`-i src/client -o dist/client`).
};

export default config;
