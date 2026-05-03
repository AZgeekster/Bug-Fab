/*!
 * Bug-Fab v0.1.0a1 — vanilla-JS frontend bundle.
 *
 * A floating action button that captures a screenshot, lets the user
 * annotate it, gathers console + network context, and POSTs a structured
 * bug report to a configured intake endpoint.
 *
 * Public API:
 *   window.BugFab.init(config)   — explicit init; auto-runs on
 *                                  DOMContentLoaded unless
 *                                  window.BugFabAutoInit === false.
 *   window.BugFab.open()         — programmatic open (capture + overlay).
 *   window.BugFab.destroy()      — remove FAB + overlay, restore globals.
 *   window.BugFab.version        — semver string.
 *
 * Requires html2canvas. By default it loads from
 *   <baseUrl>/vendor/html2canvas.min.js
 * relative to where bug-fab.js was loaded from. Override with
 * config.html2canvasUrl. If window.html2canvas is already on the page,
 * it is used as-is and no fetch happens.
 *
 * Released under MIT License — see repo/LICENSE.
 */
(() => {
  "use strict";

  // ====================================================================
  // Constants & internal state
  // ====================================================================

  const VERSION = "0.1.0a1";

  /** Attribute used to flag DOM that html2canvas should ignore. */
  const IGNORE_ATTR = "data-bug-fab-ignore";

  /** Default config — merged shallowly with caller-supplied config. */
  const DEFAULT_CONFIG = Object.freeze({
    submitUrl: null,
    headers: null,
    enabled: null,
    moduleMap: null,
    networkUrlPattern: null,
    appVersion: "",
    environment: "",
    cooldownSeconds: 30,
    bufferSize: 50,
    onSubmitSuccess: null,
    onSubmitError: null,
    html2canvasUrl: null,
  });

  /**
   * Original window.fetch saved before any wrapping. The submit POST goes
   * through this reference so the bug-reporter never logs its own traffic
   * into a subsequent report's network log.
   * (Lifted from prior extraction — audit F5 / S6.)
   */
  let __origFetch = null;

  /** Whether init() has run successfully. */
  let initialized = false;

  /** Live config (after init merge). */
  let config = { ...DEFAULT_CONFIG };

  /** FAB element + state. */
  let fab = null;
  let badge = null;
  let badgeInterval = null;
  let cooldownTimer = null;
  let cooldownRemaining = 0;
  let isCapturing = false;

  /** Overlay element + state. */
  let overlay = null;
  let annotationCanvasEl = null;
  let previouslyFocused = null;

  /** Annotation canvas state. */
  let canvasCtx = null;
  let screenshotImage = null;
  let isDrawing = false;
  let lastX = 0;
  let lastY = 0;

  /** Error/network buffers. */
  const errors = [];
  const networkLog = [];

  /** Whether buffer monkey-patches have been installed. */
  let bufferInstalled = false;

  /** Resolved base URL (where this script was loaded from). */
  let resolvedBaseUrl = null;

  // ====================================================================
  // Inline assets
  // ====================================================================

  /** SVG bug icon used in the FAB. */
  const BUG_ICON_SVG = `
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4.355.522a.5.5 0 0 1 .623.333l.291.956A5 5 0 0 1 8 1c1.007 0 1.946.298 2.731.811l.29-.956a.5.5 0 1 1 .957.29l-.41 1.352A5 5 0 0 1 13 4.519v.005A5 5 0 0 1 8 9.5a5 5 0 0 1-5-4.975V4.52A5 5 0 0 1 4.144 2.5l-.41-1.352a.5.5 0 0 1 .333-.623zM3.2 5.4a.5.5 0 0 0-.2.4v.5c0 .212.038.424.074.594.036.17.096.394.2.594a4 4 0 0 0 .555.88 3.6 3.6 0 0 0 .77.707A3.5 3.5 0 0 0 8 9.5a3.5 3.5 0 0 0 3.4-2.425c.204-.283.39-.572.555-.88.104-.2.164-.424.2-.594.036-.17.074-.382.074-.594V5.8a.5.5 0 0 0-.2-.4A4 4 0 0 0 8 4a4 4 0 0 0-4.8 1.4z"/>
      <path d="M4 0a.5.5 0 0 1 .5.5v1.634l.266-.146a5 5 0 0 1 .472-.218A5 5 0 0 1 8 1.5c.993 0 1.92.29 2.697.788l.053.032.266.146V.5a.5.5 0 0 1 1 0v2.26A5 5 0 0 1 13 5.175V6.5H3V5.175a5 5 0 0 1 .942-2.415V.5A.5.5 0 0 1 4 0z"/>
      <path d="M2.5 7a.5.5 0 0 0-.5.5v1a.5.5 0 0 0 .5.5h11a.5.5 0 0 0 .5-.5v-1a.5.5 0 0 0-.5-.5h-11zM1 10.5v1A2.5 2.5 0 0 0 3.5 14h9a2.5 2.5 0 0 0 2.5-2.5v-1H1zm2.5 2.5a1.5 1.5 0 0 1-1.415-1H13.915A1.5 1.5 0 0 1 12.5 13h-9z"/>
    </svg>
  `;

  /** Spinner SVG used in the FAB while a capture is in flight. */
  const SPINNER_SVG = `
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16" class="bug-fab-spinner" aria-hidden="true">
      <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm.25 1.03a7 7 0 0 1 0 13.94V1.03z"/>
    </svg>
  `;

  /**
   * All Bug-Fab CSS in one string. Injected once into <head>.
   * All class names prefixed with `bug-fab-` to avoid collision with
   * host CSS (Bootstrap, Pico, MUI, plain). No external stylesheet
   * is required for the bundle to render correctly.
   */
  const STYLES = `
    .bug-fab {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background-color: #f44336;
      color: #fff;
      border: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
      transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
      z-index: 9998;
      padding: 0;
      outline: none;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    }
    .bug-fab:hover {
      transform: scale(1.1);
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
    }
    .bug-fab:focus-visible {
      outline: 3px solid #fff;
      outline-offset: 3px;
    }
    .bug-fab:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }
    .bug-fab-badge {
      position: absolute;
      top: -4px;
      right: -4px;
      min-width: 20px;
      height: 20px;
      border-radius: 10px;
      background-color: #212529;
      color: #fff;
      font-size: 11px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0 5px;
      line-height: 1;
    }
    .bug-fab-badge--pulse {
      animation: bug-fab-pulse 1.5s ease-in-out infinite;
    }
    @keyframes bug-fab-pulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.2); }
    }
    @keyframes bug-fab-spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    .bug-fab-spinner {
      animation: bug-fab-spin 1s linear infinite;
    }
    /* Mobile/tablet: bigger touch target, lifted clear of bottom nav. */
    @media (max-width: 899px) {
      .bug-fab {
        width: 64px;
        height: 64px;
        bottom: 64px;
      }
    }

    /* ---- Overlay ---- */
    .bug-fab-overlay {
      position: fixed;
      inset: 0;
      z-index: 9999;
      background: rgba(0, 0, 0, 0.92);
      display: flex;
      overflow: hidden;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      color: #212529;
    }
    .bug-fab-overlay__container {
      display: flex;
      width: 100%;
      height: 100%;
      overflow: hidden;
    }
    @media (min-width: 900px) {
      .bug-fab-overlay__container {
        flex-direction: row;
      }
      .bug-fab-overlay__preview {
        flex: 0 0 58.33%;
        max-width: 58.33%;
      }
      .bug-fab-overlay__form-panel {
        flex: 0 0 41.67%;
        max-width: 41.67%;
      }
    }
    @media (max-width: 899px) {
      .bug-fab-overlay__container {
        flex-direction: column;
      }
      .bug-fab-overlay__preview {
        flex: 0 0 40%;
        max-height: 40%;
      }
      .bug-fab-overlay__form-panel {
        flex: 1 1 auto;
      }
    }
    .bug-fab-overlay__preview {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 16px;
      overflow: hidden;
      position: relative;
    }
    .bug-fab-overlay__canvas-wrap {
      position: relative;
      max-width: 100%;
      max-height: calc(100% - 56px);
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .bug-fab-overlay__canvas {
      max-width: 100%;
      max-height: 100%;
      border: 2px solid rgba(255, 255, 255, 0.2);
      border-radius: 4px;
      cursor: crosshair;
      touch-action: none;
      background: #fff;
    }
    .bug-fab-overlay__form-panel {
      background: #fff;
      overflow-y: auto;
      padding: 24px;
      box-sizing: border-box;
    }
    .bug-fab-overlay__form-panel * {
      box-sizing: border-box;
    }
    .bug-fab-overlay__form-panel h2 {
      font-size: 1.25rem;
      font-weight: 600;
      margin: 0 0 16px;
      color: #212529;
    }
    .bug-fab-field {
      margin-bottom: 12px;
    }
    .bug-fab-field label {
      display: block;
      font-size: 0.875rem;
      font-weight: 500;
      color: #212529;
      margin-bottom: 4px;
    }
    .bug-fab-field .bug-fab-required {
      color: #f44336;
    }
    .bug-fab-input,
    .bug-fab-textarea,
    .bug-fab-select {
      display: block;
      width: 100%;
      min-height: 40px;
      padding: 8px 10px;
      font-size: 0.9375rem;
      line-height: 1.4;
      color: #212529;
      background: #fff;
      border: 1px solid #ced4da;
      border-radius: 4px;
      font-family: inherit;
    }
    .bug-fab-textarea {
      min-height: 72px;
      resize: vertical;
    }
    .bug-fab-input:focus,
    .bug-fab-textarea:focus,
    .bug-fab-select:focus {
      outline: 2px solid #f44336;
      outline-offset: -1px;
      border-color: #f44336;
    }
    .bug-fab-input--invalid {
      border-color: #f44336;
      outline: 2px solid #f44336;
      outline-offset: -1px;
    }
    .bug-fab-invalid-hint {
      display: block;
      color: #f44336;
      font-size: 0.8125rem;
      margin-top: 4px;
    }
    .bug-fab-row {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    .bug-fab-actions {
      display: flex;
      gap: 12px;
      margin-top: 16px;
    }
    .bug-fab-btn {
      min-height: 44px;
      padding: 8px 16px;
      font-size: 0.9375rem;
      font-weight: 500;
      border-radius: 4px;
      border: 1px solid transparent;
      cursor: pointer;
      font-family: inherit;
    }
    .bug-fab-btn:focus-visible {
      outline: 3px solid #f44336;
      outline-offset: 2px;
    }
    .bug-fab-btn--primary {
      background: #f44336;
      color: #fff;
      flex-grow: 1;
    }
    .bug-fab-btn--primary:hover:not(:disabled) {
      background: #d32f2f;
    }
    .bug-fab-btn--secondary {
      background: #fff;
      color: #6c757d;
      border-color: #6c757d;
    }
    .bug-fab-btn--secondary:hover:not(:disabled) {
      background: #f8f9fa;
    }
    .bug-fab-btn--ghost {
      background: transparent;
      color: #fff;
      border-color: rgba(255, 255, 255, 0.6);
      margin-top: 8px;
    }
    .bug-fab-btn--ghost:hover:not(:disabled) {
      background: rgba(255, 255, 255, 0.12);
    }
    .bug-fab-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    .bug-fab-error {
      margin-top: 12px;
      padding: 10px 12px;
      background: #fdecea;
      color: #b71c1c;
      border: 1px solid #f5c2bd;
      border-radius: 4px;
      font-size: 0.875rem;
    }
    .bug-fab-error[hidden] { display: none; }
    .bug-fab-context {
      margin-top: 16px;
      border: 1px solid #e9ecef;
      border-radius: 4px;
    }
    .bug-fab-context summary {
      padding: 8px 12px;
      cursor: pointer;
      font-size: 0.875rem;
      font-weight: 500;
      color: #212529;
      list-style: none;
    }
    .bug-fab-context summary::-webkit-details-marker { display: none; }
    .bug-fab-context summary::after {
      content: " v";
      float: right;
      transform: rotate(0deg);
      transition: transform 0.15s ease;
    }
    .bug-fab-context[open] summary::after {
      content: " ^";
    }
    .bug-fab-context__body {
      padding: 8px 12px 12px;
      border-top: 1px solid #e9ecef;
      font-size: 0.8125rem;
      color: #6c757d;
    }
    .bug-fab-context__list {
      margin: 0;
      padding-left: 0;
      list-style: none;
    }
    .bug-fab-context__list li {
      padding: 2px 0;
      word-break: break-all;
    }
    .bug-fab-context__list strong {
      color: #212529;
    }
    .bug-fab-log {
      margin: 4px 0 0;
      padding: 0;
      list-style: none;
    }
    .bug-fab-log__entry {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.75rem;
      padding: 2px 4px;
      margin: 2px 0;
      background: #f8f9fa;
      border-radius: 2px;
      word-break: break-all;
    }
    .bug-fab-log__level--error { color: #b71c1c; font-weight: 600; }
    .bug-fab-log__level--warn  { color: #b15a00; font-weight: 600; }
    .bug-fab-log__status--ok   { color: #1b5e20; font-weight: 600; }
    .bug-fab-log__status--err  { color: #b71c1c; font-weight: 600; }
    .bug-fab-spinner-inline {
      display: inline-block;
      width: 14px;
      height: 14px;
      vertical-align: -2px;
      margin-right: 6px;
      border: 2px solid rgba(255, 255, 255, 0.4);
      border-top-color: #fff;
      border-radius: 50%;
      animation: bug-fab-spin 0.8s linear infinite;
    }
  `;

  // ====================================================================
  // Utilities
  // ====================================================================

  /**
   * Resolve the base URL where bug-fab.js was loaded from. Used to find
   * the vendored html2canvas.min.js sibling. Returns "" if it cannot be
   * determined (in which case the caller must supply html2canvasUrl).
   */
  const resolveBaseUrl = () => {
    if (resolvedBaseUrl !== null) return resolvedBaseUrl;
    try {
      const scripts = document.getElementsByTagName("script");
      for (let i = scripts.length - 1; i >= 0; i--) {
        const src = scripts[i].src || "";
        if (src.includes("bug-fab.js")) {
          resolvedBaseUrl = src.replace(/[^/]+$/, "");
          return resolvedBaseUrl;
        }
      }
    } catch (_e) {
      // ignore — fall through to ""
    }
    resolvedBaseUrl = "";
    return resolvedBaseUrl;
  };

  /** Inject the styles block into <head> exactly once. */
  const injectStyles = () => {
    if (document.getElementById("bug-fab-styles")) return;
    const style = document.createElement("style");
    style.id = "bug-fab-styles";
    style.textContent = STYLES;
    document.head.appendChild(style);
  };

  /** HTML-escape a string for safe insertion into innerHTML. */
  const escapeHtml = (str) => {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(String(str ?? "")));
    return div.innerHTML;
  };

  /** Resolve the headers config — supports object or () => object. */
  const resolveHeaders = () => {
    const h = config.headers;
    if (typeof h === "function") {
      try { return h() || {}; } catch (_e) { return {}; }
    }
    return h || {};
  };

  /** Should the FAB be visible right now? */
  const isEnabled = () => {
    // Honor the literal boolean BEFORE the function-call branch — passing
    // `enabled: false` was previously a silent no-op because `isEnabled()`
    // only treated `enabled` as a gate when it was a callable. Surfaced
    // by a 2026-05-03 consumer-integration audit.
    if (config.enabled === false) return false;
    if (config.enabled === true) return true;
    if (typeof config.enabled === "function") {
      try { return Boolean(config.enabled()); } catch (_e) { return false; }
    }
    return true;
  };

  /** Promise wrapper for dynamic <script> injection. */
  const loadScript = (src) =>
    new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Failed to load: ${src}`));
      document.head.appendChild(script);
    });

  /** Load html2canvas (cached on window once present). */
  const loadHtml2Canvas = async () => {
    if (typeof window.html2canvas === "function") return window.html2canvas;
    const url = config.html2canvasUrl || `${resolveBaseUrl()}vendor/html2canvas.min.js`;
    await loadScript(url);
    if (typeof window.html2canvas !== "function") {
      throw new Error("html2canvas loaded but not available on window");
    }
    return window.html2canvas;
  };

  // ====================================================================
  // Error / network buffer (with __origFetch + .stack — audit F5/F6/S6)
  // ====================================================================

  /**
   * Push an entry into the error buffer, evicting oldest when the cap
   * is reached.
   */
  const pushError = (entry) => {
    errors.push(entry);
    while (errors.length > config.bufferSize) errors.shift();
  };

  /** Push a network entry, with the same eviction rule. */
  const pushNetwork = (entry) => {
    networkLog.push(entry);
    while (networkLog.length > config.bufferSize) networkLog.shift();
  };

  /** Stringify a console.* argument list into a single message string. */
  const stringifyArgs = (args) =>
    Array.prototype.map.call(args, (a) => {
      if (a instanceof Error) return a.message;
      try {
        return typeof a === "string" ? a : JSON.stringify(a);
      } catch (_e) {
        return String(a);
      }
    }).join(" ");

  /**
   * Capture a stack trace from an Error if present in the args, else
   * synthesize one. Lifted from prior extraction (audit F6 / S6).
   */
  const captureStack = (args) => {
    for (const a of args) {
      if (a instanceof Error && a.stack) return a.stack;
    }
    try {
      throw new Error("__bug_fab_trace__");
    } catch (e) {
      // Strip the first two lines (the throw + this fn) for cleanliness.
      const lines = (e.stack || "").split("\n");
      return lines.slice(2).join("\n");
    }
  };

  /** Install console + window + fetch interceptors. Idempotent. */
  const installBuffer = () => {
    if (bufferInstalled) return;
    bufferInstalled = true;

    // Save the *original* fetch BEFORE any wrapping so submit() can use
    // it without self-logging the bug-report POST. (Audit F5 / S6.)
    __origFetch = window.fetch ? window.fetch.bind(window) : null;

    const origError = console.error;
    const origWarn = console.warn;

    console.error = function (...args) {
      pushError({
        level: "error",
        message: stringifyArgs(args),
        stack: captureStack(args),
        timestamp: new Date().toISOString(),
      });
      return origError.apply(console, args);
    };

    console.warn = function (...args) {
      pushError({
        level: "warn",
        message: stringifyArgs(args),
        stack: captureStack(args),
        timestamp: new Date().toISOString(),
      });
      return origWarn.apply(console, args);
    };

    window.addEventListener("error", (event) => {
      pushError({
        level: "error",
        message: `${event.message || "Unknown error"} at ${event.filename || "unknown"}:${event.lineno || 0}`,
        stack: (event.error && event.error.stack) || "",
        timestamp: new Date().toISOString(),
      });
    });

    window.addEventListener("unhandledrejection", (event) => {
      const reason = event.reason;
      let message = "Unhandled promise rejection: ";
      let stack = "";
      if (reason instanceof Error) {
        message += reason.message;
        stack = reason.stack || "";
      } else if (typeof reason === "string") {
        message += reason;
      } else {
        try { message += JSON.stringify(reason); }
        catch (_e) { message += String(reason); }
      }
      pushError({
        level: "error",
        message,
        stack,
        timestamp: new Date().toISOString(),
      });
    });

    // Wrap fetch for network logging. The submit POST uses __origFetch
    // directly, so it won't be captured here.
    if (__origFetch) {
      window.fetch = function (input, init) {
        const urlStr = typeof input === "string"
          ? input
          : (input && input.url) || "";

        const pattern = config.networkUrlPattern;
        if (pattern && !pattern.test(urlStr)) {
          return __origFetch.call(this, input, init);
        }

        const entry = {
          url: urlStr,
          method: (init && init.method) || (input && input.method) || "GET",
          status: null,
          duration: null,
          timestamp: new Date().toISOString(),
        };
        const start = performance.now();

        return __origFetch.call(this, input, init).then(
          (response) => {
            entry.status = response.status;
            entry.duration = Math.round(performance.now() - start);
            pushNetwork(entry);
            return response;
          },
          (err) => {
            entry.status = 0;
            entry.duration = Math.round(performance.now() - start);
            entry.error = err && err.message ? err.message : String(err);
            pushNetwork(entry);
            throw err;
          }
        );
      };
    }
  };

  /** Count of `error`-level entries (for badge display). */
  const getErrorCount = () =>
    errors.reduce((n, e) => (e.level === "error" ? n + 1 : n), 0);

  /** Empty both buffers — called after a successful submission. */
  const clearBuffers = () => {
    errors.length = 0;
    networkLog.length = 0;
  };

  // ====================================================================
  // Module detection
  // ====================================================================

  /**
   * Resolve a "module" label from window.location.pathname. Looks up the
   * config.moduleMap (a {prefix: label} object) first; otherwise returns
   * the first non-empty path segment, capitalized; otherwise null.
   */
  const detectModule = (pathname) => {
    const path = (pathname || window.location.pathname || "").toLowerCase();
    const map = config.moduleMap;
    if (map && typeof map === "object") {
      // Order by descending key length so the longest prefix wins.
      const keys = Object.keys(map).sort((a, b) => b.length - a.length);
      for (const key of keys) {
        if (path.startsWith(key.toLowerCase())) return map[key];
      }
    }
    const segments = path.split("/").filter(Boolean);
    if (segments.length === 0) return "";
    const seg = segments[0];
    return seg.charAt(0).toUpperCase() + seg.slice(1);
  };

  // ====================================================================
  // FAB
  // ====================================================================

  /** Build the FAB and append it to <body>. */
  const createFab = () => {
    fab = document.createElement("button");
    fab.className = "bug-fab";
    fab.type = "button";
    fab.setAttribute("aria-label", "Report a bug");
    fab.setAttribute("title", "Report a bug");
    fab.setAttribute(IGNORE_ATTR, "");
    fab.innerHTML = BUG_ICON_SVG;

    badge = document.createElement("span");
    badge.className = "bug-fab-badge";
    badge.style.display = "none";
    badge.setAttribute("aria-live", "polite");
    fab.appendChild(badge);

    fab.addEventListener("click", () => { openOverlayCapture(); });

    document.body.appendChild(fab);
  };

  /** Refresh the badge to reflect the current error count. */
  const updateBadge = () => {
    if (!badge) return;
    const count = getErrorCount();
    if (count > 0) {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.style.display = "flex";
      badge.classList.add("bug-fab-badge--pulse");
    } else {
      badge.style.display = "none";
      badge.classList.remove("bug-fab-badge--pulse");
    }
  };

  const startBadgePolling = () => {
    updateBadge();
    badgeInterval = window.setInterval(updateBadge, 3000);
  };

  /** Show the spinner SVG (preserves the badge). */
  const showFabSpinner = () => {
    if (!fab) return;
    fab.innerHTML = SPINNER_SVG;
    if (badge) fab.appendChild(badge);
  };

  /** Restore the bug icon (preserves the badge). */
  const hideFabSpinner = () => {
    if (!fab) return;
    fab.innerHTML = BUG_ICON_SVG;
    if (badge) fab.appendChild(badge);
  };

  /**
   * Cooldown the FAB after a successful submit so users can't spam-flood
   * the intake. Configurable; default 30s. Badge counts down.
   */
  const startCooldown = () => {
    if (!fab) return;
    cooldownRemaining = Math.max(0, config.cooldownSeconds | 0);
    if (cooldownRemaining === 0) return;

    fab.disabled = true;
    fab.setAttribute("aria-label", `Bug report submitted. Wait ${cooldownRemaining}s`);
    fab.title = `Wait ${cooldownRemaining}s`;
    if (badge) {
      badge.textContent = String(cooldownRemaining);
      badge.style.display = "flex";
      badge.classList.remove("bug-fab-badge--pulse");
    }

    cooldownTimer = window.setInterval(() => {
      cooldownRemaining--;
      if (cooldownRemaining <= 0) {
        window.clearInterval(cooldownTimer);
        cooldownTimer = null;
        fab.disabled = false;
        fab.setAttribute("aria-label", "Report a bug");
        fab.title = "Report a bug";
        hideFabSpinner();
        updateBadge();
      } else {
        fab.setAttribute("aria-label", `Wait ${cooldownRemaining}s`);
        fab.title = `Wait ${cooldownRemaining}s`;
        if (badge) badge.textContent = String(cooldownRemaining);
      }
    }, 1000);
  };

  // ====================================================================
  // Annotation canvas
  // ====================================================================

  /** Compute a canvas-coordinate position from a mouse/pointer event. */
  const getMousePos = (e) => {
    const rect = annotationCanvasEl.getBoundingClientRect();
    const scaleX = annotationCanvasEl.width / rect.width;
    const scaleY = annotationCanvasEl.height / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    };
  };

  /** Compute a canvas-coordinate position from a touch event. */
  const getTouchPos = (e) => {
    const rect = annotationCanvasEl.getBoundingClientRect();
    const scaleX = annotationCanvasEl.width / rect.width;
    const scaleY = annotationCanvasEl.height / rect.height;
    const touch = e.touches[0] || e.changedTouches[0];
    return {
      x: (touch.clientX - rect.left) * scaleX,
      y: (touch.clientY - rect.top) * scaleY,
    };
  };

  const startDraw = (pos) => {
    isDrawing = true;
    lastX = pos.x;
    lastY = pos.y;
  };

  const continueDraw = (pos) => {
    if (!isDrawing || !canvasCtx) return;
    canvasCtx.strokeStyle = "#f44336";
    canvasCtx.lineWidth = 3;
    canvasCtx.lineCap = "round";
    canvasCtx.lineJoin = "round";
    canvasCtx.beginPath();
    canvasCtx.moveTo(lastX, lastY);
    canvasCtx.lineTo(pos.x, pos.y);
    canvasCtx.stroke();
    lastX = pos.x;
    lastY = pos.y;
  };

  const stopDraw = () => { isDrawing = false; };

  const onMouseDown = (e) => { e.preventDefault(); startDraw(getMousePos(e)); };
  const onMouseMove = (e) => {
    if (!isDrawing) return;
    e.preventDefault();
    continueDraw(getMousePos(e));
  };
  const onMouseUp = (e) => { e.preventDefault(); stopDraw(); };
  const onTouchStart = (e) => {
    e.preventDefault();
    if (e.touches.length === 1) startDraw(getTouchPos(e));
  };
  const onTouchMove = (e) => {
    e.preventDefault();
    if (!isDrawing || e.touches.length !== 1) return;
    continueDraw(getTouchPos(e));
  };
  const onTouchEnd = (e) => { e.preventDefault(); stopDraw(); };

  /** Bind drawing events to the annotation canvas. */
  const bindCanvasEvents = () => {
    if (!annotationCanvasEl) return;
    annotationCanvasEl.addEventListener("mousedown", onMouseDown);
    annotationCanvasEl.addEventListener("mousemove", onMouseMove);
    annotationCanvasEl.addEventListener("mouseup", onMouseUp);
    annotationCanvasEl.addEventListener("mouseleave", onMouseUp);
    annotationCanvasEl.addEventListener("touchstart", onTouchStart, { passive: false });
    annotationCanvasEl.addEventListener("touchmove", onTouchMove, { passive: false });
    annotationCanvasEl.addEventListener("touchend", onTouchEnd, { passive: false });
    annotationCanvasEl.addEventListener("touchcancel", onTouchEnd, { passive: false });
  };

  /** Unbind drawing events on close / re-init. */
  const unbindCanvasEvents = () => {
    if (!annotationCanvasEl) return;
    annotationCanvasEl.removeEventListener("mousedown", onMouseDown);
    annotationCanvasEl.removeEventListener("mousemove", onMouseMove);
    annotationCanvasEl.removeEventListener("mouseup", onMouseUp);
    annotationCanvasEl.removeEventListener("mouseleave", onMouseUp);
    annotationCanvasEl.removeEventListener("touchstart", onTouchStart);
    annotationCanvasEl.removeEventListener("touchmove", onTouchMove);
    annotationCanvasEl.removeEventListener("touchend", onTouchEnd);
    annotationCanvasEl.removeEventListener("touchcancel", onTouchEnd);
  };

  /** Draw the screenshot as the canvas background layer. */
  const drawScreenshot = () => {
    if (!canvasCtx || !screenshotImage) return;
    canvasCtx.drawImage(screenshotImage, 0, 0, annotationCanvasEl.width, annotationCanvasEl.height);
  };

  /**
   * Initialize the annotation canvas with a screenshot source. Source can
   * be an HTMLCanvasElement (the typical html2canvas return value), an
   * HTMLImageElement, or a URL/data-URL string.
   */
  const initAnnotationCanvas = (canvasEl, imageSource) => {
    if (annotationCanvasEl) unbindCanvasEvents();
    annotationCanvasEl = canvasEl;
    canvasCtx = canvasEl.getContext("2d");
    screenshotImage = new Image();

    if (imageSource instanceof HTMLCanvasElement) {
      screenshotImage.src = imageSource.toDataURL("image/png");
    } else if (imageSource instanceof HTMLImageElement) {
      screenshotImage.src = imageSource.src;
    } else if (typeof imageSource === "string") {
      screenshotImage.src = imageSource;
    }

    const onReady = () => {
      annotationCanvasEl.width = screenshotImage.naturalWidth || screenshotImage.width;
      annotationCanvasEl.height = screenshotImage.naturalHeight || screenshotImage.height;
      drawScreenshot();
      bindCanvasEvents();
    };

    screenshotImage.onload = onReady;
    if (screenshotImage.complete && screenshotImage.naturalWidth > 0) onReady();
  };

  /** Wipe annotations, restoring the original screenshot. */
  const clearAnnotations = () => {
    if (!canvasCtx || !screenshotImage) return;
    canvasCtx.clearRect(0, 0, annotationCanvasEl.width, annotationCanvasEl.height);
    drawScreenshot();
  };

  /** Resolve to a PNG Blob containing the screenshot + annotations. */
  const getCompositeImage = () =>
    new Promise((resolve, reject) => {
      if (!annotationCanvasEl) {
        reject(new Error("Canvas not initialized"));
        return;
      }
      annotationCanvasEl.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error("Failed to create image blob"));
      }, "image/png");
    });

  // ====================================================================
  // Overlay
  // ====================================================================

  /** Build the structured `context` block included in the report. */
  const gatherContext = () => ({
    url: window.location.href,
    module: detectModule(),
    user_agent: navigator.userAgent,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    console_errors: errors.slice(),
    network_log: networkLog.slice(),
    app_version: config.appVersion || "",
    environment: config.environment || "",
  });

  /**
   * Render the auto-context disclosure section.
   * @param {object} context - The gathered context.
   * @returns {string} HTML.
   */
  const buildContextSection = (context) => {
    const errorCount = context.console_errors.length;
    const networkCount = context.network_log.length;

    let html = `
      <details class="bug-fab-context">
        <summary>Auto-Captured Context</summary>
        <div class="bug-fab-context__body">
          <ul class="bug-fab-context__list">
            <li><strong>URL:</strong> ${escapeHtml(context.url)}</li>
            <li><strong>Module:</strong> ${escapeHtml(context.module ?? "(none)")}</li>
            <li><strong>Browser:</strong> ${escapeHtml(context.user_agent)}</li>
            <li><strong>Viewport:</strong> ${context.viewport_width} x ${context.viewport_height}</li>
            ${context.app_version ? `<li><strong>App version:</strong> ${escapeHtml(context.app_version)}</li>` : ""}
            ${context.environment ? `<li><strong>Environment:</strong> ${escapeHtml(context.environment)}</li>` : ""}
          </ul>
          <p style="margin: 8px 0 0; font-weight: 600; color: #212529; font-size: 0.8125rem;">
            Console (${errorCount})
          </p>
          <ul class="bug-fab-log">`;

    if (errorCount === 0) {
      html += `<li class="bug-fab-log__entry">No console events captured</li>`;
    } else {
      for (const err of context.console_errors) {
        const lvlClass = err.level === "error" ? "bug-fab-log__level--error" : "bug-fab-log__level--warn";
        html += `<li class="bug-fab-log__entry">
          <span class="${lvlClass}">${escapeHtml((err.level || "").toUpperCase())}</span>
          ${escapeHtml(err.message)}
        </li>`;
      }
    }

    html += `</ul>
          <p style="margin: 8px 0 0; font-weight: 600; color: #212529; font-size: 0.8125rem;">
            Network (${networkCount})
          </p>
          <ul class="bug-fab-log">`;

    if (networkCount === 0) {
      html += `<li class="bug-fab-log__entry">No network calls captured</li>`;
    } else {
      for (const net of context.network_log) {
        let statusClass = "";
        if (net.status >= 400 || net.status === 0) statusClass = "bug-fab-log__status--err";
        else if (net.status >= 200 && net.status < 300) statusClass = "bug-fab-log__status--ok";
        const dur = net.duration !== null && net.duration !== undefined ? ` (${net.duration}ms)` : "";
        html += `<li class="bug-fab-log__entry">
          <span class="${statusClass}">${net.status || "ERR"}</span>
          ${escapeHtml(net.method)} ${escapeHtml(net.url)}${dur}
        </li>`;
      }
    }

    html += `</ul></div></details>`;
    return html;
  };

  /** Build the overlay DOM tree. Returns the root element + context. */
  const buildOverlay = () => {
    const context = gatherContext();

    overlay = document.createElement("div");
    overlay.className = "bug-fab-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Report a Bug");
    overlay.setAttribute(IGNORE_ATTR, "");

    overlay.innerHTML = `
      <div class="bug-fab-overlay__container">
        <div class="bug-fab-overlay__preview">
          <div class="bug-fab-overlay__canvas-wrap">
            <canvas class="bug-fab-overlay__canvas"
              aria-label="Screenshot annotation canvas. Draw to annotate."></canvas>
          </div>
          <button type="button" class="bug-fab-btn bug-fab-btn--ghost"
            data-bug-fab-clear aria-label="Clear all annotations">
            Clear Annotations
          </button>
        </div>
        <div class="bug-fab-overlay__form-panel">
          <h2>Report a Bug</h2>
          <form data-bug-fab-form novalidate>
            <div class="bug-fab-field">
              <label for="bug-fab-title">Title <span class="bug-fab-required" aria-hidden="true">*</span></label>
              <input type="text" id="bug-fab-title" name="title" class="bug-fab-input"
                required maxlength="200" autocomplete="off"
                placeholder="Brief description of the issue" aria-required="true">
              <span class="bug-fab-invalid-hint" hidden>Please enter a title.</span>
            </div>
            <div class="bug-fab-field">
              <label for="bug-fab-description">Description</label>
              <textarea id="bug-fab-description" name="description" class="bug-fab-textarea"
                rows="3" maxlength="2000"
                placeholder="Steps to reproduce, what happened..."></textarea>
            </div>
            <div class="bug-fab-field">
              <label for="bug-fab-expected">Expected Behavior</label>
              <textarea id="bug-fab-expected" name="expected_behavior" class="bug-fab-textarea"
                rows="2" maxlength="1000"
                placeholder="What should have happened instead..."></textarea>
            </div>
            <div class="bug-fab-row">
              <div class="bug-fab-field">
                <label for="bug-fab-type">Type</label>
                <select id="bug-fab-type" name="report_type" class="bug-fab-select">
                  <option value="bug" selected>Bug Report</option>
                  <option value="feature_request">Feature Request</option>
                </select>
              </div>
              <div class="bug-fab-field">
                <label for="bug-fab-severity">Severity</label>
                <select id="bug-fab-severity" name="severity" class="bug-fab-select">
                  <option value="low">Low</option>
                  <option value="medium" selected>Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div class="bug-fab-field">
                <label for="bug-fab-tags">Tags</label>
                <input type="text" id="bug-fab-tags" name="tags" class="bug-fab-input"
                  placeholder="ui, data" autocomplete="off">
              </div>
            </div>
            ${buildContextSection(context)}
            <div class="bug-fab-actions">
              <button type="submit" class="bug-fab-btn bug-fab-btn--primary"
                data-bug-fab-submit aria-label="Submit bug report">
                Submit Bug Report
              </button>
              <button type="button" class="bug-fab-btn bug-fab-btn--secondary"
                data-bug-fab-cancel aria-label="Cancel bug report">
                Cancel
              </button>
            </div>
            <div class="bug-fab-error" data-bug-fab-error role="alert" hidden></div>
          </form>
        </div>
      </div>
    `;

    return { overlay, context };
  };

  /** Tab-key focus trap inside the overlay. */
  const FOCUSABLE_SELECTORS =
    'a[href], button:not([disabled]), textarea, input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

  const trapFocus = (e) => {
    if (!overlay) return;
    const focusable = overlay.querySelectorAll(FOCUSABLE_SELECTORS);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.key === "Tab") {
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Escape") closeOverlay();
    trapFocus(e);
  };

  /**
   * Parse the comma-separated tags input into a clean array of strings.
   */
  const parseTags = (value) => {
    if (!value) return [];
    return value.split(",").map((t) => t.trim()).filter(Boolean);
  };

  /**
   * Submit the report. Uses __origFetch (saved before our wrapper) so
   * the POST does not show up in the next report's network_log.
   */
  const submitReport = async (context) => {
    const titleInput = overlay.querySelector("#bug-fab-title");
    const errorBox = overlay.querySelector("[data-bug-fab-error]");
    const submitBtn = overlay.querySelector("[data-bug-fab-submit]");
    const titleHint = overlay.querySelector(".bug-fab-invalid-hint");

    const title = titleInput.value.trim();
    if (!title) {
      titleInput.classList.add("bug-fab-input--invalid");
      if (titleHint) titleHint.hidden = false;
      titleInput.focus();
      return;
    }
    titleInput.classList.remove("bug-fab-input--invalid");
    if (titleHint) titleHint.hidden = true;

    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="bug-fab-spinner-inline" aria-hidden="true"></span>Submitting...`;
    errorBox.hidden = true;
    errorBox.textContent = "";

    const metadata = {
      protocol_version: "0.1",
      title,
      client_ts: new Date().toISOString(),
      report_type: overlay.querySelector("#bug-fab-type").value,
      description: overlay.querySelector("#bug-fab-description").value.trim(),
      expected_behavior: overlay.querySelector("#bug-fab-expected").value.trim(),
      severity: overlay.querySelector("#bug-fab-severity").value,
      tags: parseTags(overlay.querySelector("#bug-fab-tags").value),
      context,
    };

    try {
      const blob = await getCompositeImage();
      const formData = new FormData();
      formData.append("metadata", JSON.stringify(metadata));
      formData.append("screenshot", blob, "screenshot.png");

      const fetcher = __origFetch || window.fetch.bind(window);
      const response = await fetcher(config.submitUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: resolveHeaders(),
        body: formData,
      });

      if (!response.ok) {
        let message = `Submission failed (${response.status})`;
        try {
          const text = await response.text();
          try {
            const parsed = JSON.parse(text);
            const detail = parsed && parsed.detail;
            if (typeof detail === "string") {
              message = detail;
            } else if (Array.isArray(detail) && detail.length > 0) {
              // FastAPI / Pydantic 422 returns an array of {loc, msg, type, ...}
              // objects. Render the first error in a human-readable form
              // instead of letting the toast string-coerce it to "[object Object]".
              const first = detail[0];
              const loc = Array.isArray(first.loc) ? first.loc.filter((p) => p !== "body").join(".") : "";
              const msg = first.msg || JSON.stringify(first);
              message = loc ? `${loc}: ${msg}` : msg;
              if (detail.length > 1) message += ` (+${detail.length - 1} more)`;
            }
          } catch (_e) { /* keep default */ }
        } catch (_e) { /* keep default */ }
        throw new Error(message);
      }

      let result = null;
      try { result = await response.json(); } catch (_e) { result = null; }

      // Success — clean up and notify.
      clearBuffers();
      closeOverlay();
      if (typeof config.onSubmitSuccess === "function") {
        try { config.onSubmitSuccess(result); } catch (_e) { /* swallow */ }
      }
      if (fab) startCooldown();
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit Bug Report";
      errorBox.hidden = false;
      errorBox.textContent = err && err.message
        ? err.message
        : "Failed to submit bug report. Please try again.";
      if (typeof config.onSubmitError === "function") {
        try { config.onSubmitError(err); } catch (_e) { /* swallow */ }
      }
    }
  };

  /** Open the overlay using a freshly captured screenshot. */
  const openOverlayWithScreenshot = (screenshotCanvas) => {
    if (overlay) return;
    injectStyles();
    previouslyFocused = document.activeElement;

    const built = buildOverlay();
    overlay = built.overlay;
    const context = built.context;
    document.body.appendChild(overlay);
    document.body.style.overflow = "hidden";

    const canvasEl = overlay.querySelector(".bug-fab-overlay__canvas");
    initAnnotationCanvas(canvasEl, screenshotCanvas);

    overlay.querySelector("[data-bug-fab-clear]")
      .addEventListener("click", clearAnnotations);
    overlay.querySelector("[data-bug-fab-cancel]")
      .addEventListener("click", closeOverlay);

    overlay.querySelector("[data-bug-fab-form]")
      .addEventListener("submit", (e) => {
        e.preventDefault();
        submitReport(context);
      });

    const titleInput = overlay.querySelector("#bug-fab-title");
    const titleHint = overlay.querySelector(".bug-fab-invalid-hint");
    titleInput.addEventListener("input", () => {
      titleInput.classList.remove("bug-fab-input--invalid");
      if (titleHint) titleHint.hidden = true;
    });

    document.addEventListener("keydown", onKeyDown);
    setTimeout(() => titleInput.focus(), 100);
  };

  /** Tear down the overlay and restore focus. */
  const closeOverlay = () => {
    if (!overlay) return;
    document.removeEventListener("keydown", onKeyDown);
    unbindCanvasEvents();
    annotationCanvasEl = null;
    canvasCtx = null;
    screenshotImage = null;
    isDrawing = false;
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    overlay = null;
    document.body.style.overflow = "";
    if (previouslyFocused && typeof previouslyFocused.focus === "function") {
      previouslyFocused.focus();
    }
    previouslyFocused = null;
  };

  // ====================================================================
  // Capture flow
  // ====================================================================

  /**
   * Capture a screenshot then open the overlay. Used both by the FAB
   * click handler and by window.BugFab.open().
   *
   * Lifts the viewport-clipping fix from audit F18: without the x/y/
   * width/height + scrollX/scrollY options, sticky/fixed elements get
   * double-rendered and the screenshot becomes a tall mostly-white
   * image. Clipping to the actual viewport produces a 1:1 capture.
   */
  const openOverlayCapture = async () => {
    if (isCapturing || (fab && fab.disabled)) return;
    if (!config.submitUrl) {
      const msg = "Bug-Fab: submitUrl is required.";
      if (typeof config.onSubmitError === "function") {
        try { config.onSubmitError(new Error(msg)); } catch (_e) { /* swallow */ }
      }
      console.error(msg);
      return;
    }

    isCapturing = true;
    if (fab) showFabSpinner();

    try {
      const html2canvas = await loadHtml2Canvas();
      const screenshotCanvas = await html2canvas(document.body, {
        ignoreElements: (el) => el.hasAttribute(IGNORE_ATTR),
        useCORS: true,
        logging: false,
        scale: 1,
        backgroundColor: "#ffffff",
        x: window.scrollX,
        y: window.scrollY,
        width: window.innerWidth,
        height: window.innerHeight,
        windowWidth: document.documentElement.clientWidth,
        windowHeight: document.documentElement.clientHeight,
        scrollX: -window.scrollX,
        scrollY: -window.scrollY,
      });
      hideFabSpinner();
      isCapturing = false;
      openOverlayWithScreenshot(screenshotCanvas);
    } catch (err) {
      hideFabSpinner();
      isCapturing = false;
      console.error("Bug-Fab screenshot failed:", err && err.message ? err.message : err);
      if (typeof config.onSubmitError === "function") {
        try { config.onSubmitError(err); } catch (_e) { /* swallow */ }
      }
    }
  };

  // ====================================================================
  // Public API
  // ====================================================================

  /**
   * Initialize Bug-Fab. Idempotent — safe to call multiple times; only
   * the first call has effect.
   *
   * @param {object} [userConfig] - See DEFAULT_CONFIG for keys.
   */
  const init = (userConfig = {}) => {
    if (initialized) return;
    config = { ...DEFAULT_CONFIG, ...userConfig };

    // Buffer must install ASAP so it captures errors fired before the
    // user opens the FAB. (Doing it inside init() rather than at script
    // load keeps tests / SPAs free to no-op the bundle if they want.)
    installBuffer();

    if (!isEnabled()) {
      // Caller's predicate says no FAB right now. Buffers still record;
      // a later init() call with a different predicate would be a no-op,
      // which matches "explicit init runs once."
      initialized = true;
      return;
    }

    injectStyles();
    createFab();
    startBadgePolling();
    initialized = true;
  };

  /**
   * Programmatic open — captures a screenshot and opens the overlay,
   * exactly as a FAB click would.
   */
  const open = () => { openOverlayCapture(); };

  /**
   * Tear everything down. Removes the FAB and overlay from the DOM,
   * clears intervals, restores window.fetch and console.* to their
   * pre-init originals (best effort), and resets state. Useful for
   * SPAs that mount/unmount.
   */
  const destroy = () => {
    if (badgeInterval) {
      window.clearInterval(badgeInterval);
      badgeInterval = null;
    }
    if (cooldownTimer) {
      window.clearInterval(cooldownTimer);
      cooldownTimer = null;
    }
    if (fab && fab.parentNode) fab.parentNode.removeChild(fab);
    fab = null;
    badge = null;
    closeOverlay();
    if (__origFetch) window.fetch = __origFetch;
    initialized = false;
  };

  window.BugFab = Object.freeze({
    init,
    open,
    destroy,
    version: VERSION,
  });

  // Auto-init on DOMContentLoaded unless caller opts out.
  const autoInit = () => {
    if (window.BugFabAutoInit === false) return;
    if (initialized) return;
    // If the caller already invoked init() with custom config before
    // DOMContentLoaded, initialized will be true and this is a no-op.
    init({});
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    // Defer to next tick so the caller has a chance to set
    // window.BugFabAutoInit = false before we run.
    window.setTimeout(autoInit, 0);
  }
})();
