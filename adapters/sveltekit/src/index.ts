// Top-level barrel for `bug-fab-sveltekit`.
//
// Most consumers should import from `bug-fab-sveltekit/server` (handler
// factories + storage) and `bug-fab-sveltekit/client` (the BugFab Svelte
// component). This barrel re-exports server APIs for convenience but does
// NOT re-export client APIs — pulling Svelte component code into a server
// bundle is wasteful and can cause SSR confusion.
//
// Use:
//   import { createIntakeHandler, FileStorage } from 'bug-fab-sveltekit/server';
//   import BugFab from 'bug-fab-sveltekit/client';

export * from './server/index.js';
