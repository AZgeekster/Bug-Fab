// Shared Bug-Fab adapter wiring for the example app.
//
// This file is the single source of truth for the adapter configuration
// across all of the example's `+server.ts` route handlers. Importing it
// from each route file ensures the same `IStorage` instance is reused.
//
// Tip: in production, you'll want a singleton DB-backed storage rather than
// FileStorage so multiple Node workers share state. See the package's
// DrizzleStorage backend.

import { FileStorage } from 'bug-fab-sveltekit/server';
import type { BugFabAdapterOptions } from 'bug-fab-sveltekit/server';

export const storage = new FileStorage({
  storageDir: process.env.BUG_FAB_DIR ?? './var/bug-reports'
});

/**
 * Adapter options shared by all `+server.ts` factories. Add `github` config,
 * `resolveActor`, or `maxScreenshotBytes` here.
 */
export const adapterOptions: BugFabAdapterOptions = {
  storage,
  // Example: pull the actor identity from a session cookie populated by
  // the consumer's auth in `hooks.server.ts`.
  resolveActor: (event) => {
    const locals = (event as { locals?: { user?: { email?: string } } }).locals;
    return locals?.user?.email ?? null;
  }
};
