// Bug-Fab vanilla-JS frontend bundle.
//
// Replace this placeholder with the upstream Bug-Fab JS bundle. At
// gem-build time this file is replaced with a copy of the upstream
// `repo/static/bug-fab.js` from the Bug-Fab repository. The build script
// (Rakefile task `bug_fab:vendor_js`, planned for v0.2) fetches the
// bundle that matches `BugFab::PROTOCOL_VERSION` and writes it here.
//
// This placeholder is intentionally tiny so a fresh checkout can boot
// without running the vendor task. The placeholder logs a warning and
// no-ops the FAB; a real consumer ships the real bundle.
//
// To use the real bundle in development, drop the upstream file at:
//   app/assets/javascripts/bug_fab/bug-fab.js
//
// Then reference it from the consuming app:
//   <%= javascript_include_tag "bug_fab/bug-fab" %>

(function () {
  if (typeof window === "undefined") return;
  if (window.__bugFabPlaceholderLogged) return;
  window.__bugFabPlaceholderLogged = true;
  console.warn(
    "[bug_fab-rails] Frontend bundle placeholder is active. " +
    "Copy the matching upstream `repo/static/bug-fab.js` into " +
    "`app/assets/javascripts/bug_fab/bug-fab.js` to enable the FAB."
  );
})();
