// Vite config for the conformance SvelteKit app. The only job is wiring
// the SvelteKit plugin; everything else (port, host) is controlled by
// adapter-node at runtime via PORT / HOST env vars.
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()]
});
