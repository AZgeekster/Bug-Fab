// SvelteKit app-level type declarations for the conformance app.
// Kept intentionally empty — the route handlers don't read `event.locals`
// because conformance doesn't exercise the auth path.
// See https://kit.svelte.dev/docs/types#app
declare global {
  namespace App {
    // interface Error {}
    // interface Locals {}
    // interface PageData {}
    // interface Platform {}
  }
}

export {};
