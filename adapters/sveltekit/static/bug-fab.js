// REPLACE ME — vendored Bug-Fab browser bundle.
//
// This placeholder ships in the draft so the build pipeline produces a
// non-empty `dist/static/bug-fab.js` even before the upstream `bug-fab` PyPI
// package is published. Two ways to replace it:
//
// 1. Run `pnpm run vendor:bundle` before `pnpm build` — copies the canonical
//    bundle from the upstream Bug-Fab monorepo's `repo/static/bug-fab.js`
//    into this directory. (See package.json scripts.)
//
// 2. Manually `pip install bug-fab && cp $(python -c "import bug_fab,os;print(os.path.dirname(bug_fab.__file__))")/static/bug-fab.js .`
//
// Until vendored, this file is a no-op stub: it defines a stand-in
// `window.BugFab.init` that logs a warning so consumers don't get a silent
// "FAB never appears" failure.
(function () {
  'use strict';
  if (typeof window === 'undefined') return;
  if (window.BugFab && typeof window.BugFab.init === 'function') return;
  window.BugFab = window.BugFab || {};
  window.BugFab.init = function () {
    // eslint-disable-next-line no-console
    console.warn(
      '[bug-fab-sveltekit] The vendored bug-fab.js placeholder is still in place. ' +
        'Run `pnpm run vendor:bundle` (or replace static/bug-fab.js manually) before publishing.'
    );
  };
})();
