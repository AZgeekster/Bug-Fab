// svelte.config.js for the bug-fab-sveltekit package itself.
// Consumers ship their own svelte.config.js; this one only affects packaging
// of the bundled <BugFab /> Svelte component.
import { vitePreprocess } from '@sveltejs/kit/vite';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // No kit adapter is configured — this is a library, not an app.
  },
  package: {
    source: 'src/client',
    dir: 'dist/client',
    emitTypes: true
  }
};

export default config;
