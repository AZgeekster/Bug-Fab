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
 *                                  config.enabled accepts boolean OR
 *                                  () => boolean. (FAB UX TH-7.)
 *   window.BugFab.open()         — programmatic open (capture + overlay).
 *   window.BugFab.disable()      — hide the FAB at runtime; closes any
 *                                  open overlay. Idempotent. (TH-7.)
 *   window.BugFab.enable()       — re-show the FAB at runtime; lazily
 *                                  creates it if init() ran while
 *                                  disabled. Idempotent. (TH-7.)
 *   window.BugFab.destroy()      — remove FAB + overlay, restore globals.
 *   window.BugFab.version        — semver string.
 *
 * Bundle <script> tag may also carry `data-bug-fab-disabled="true"` to
 * flip the kill-switch from non-JS templates. (FAB UX TH-7.)
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
    // Annotation tools (TH-14): stroke color used by free-draw, rectangle,
    // and arrow tools. Defaults to the same red the v0.1 free-draw used.
    annotationColor: "#f44336",
    // FAB UX (TH-5/6/15)
    position: "bottom-right",
    stackAbove: null,
    stackBelow: null,
    stackLeft: null,
    stackRight: null,
    gap: 12,
    categories: null,
    categoryLabel: "Category",
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

  /**
   * FAB UX (TH-5/6/7): runtime disable + anchor-to-element bookkeeping.
   * `userDisabled` is a kill-switch flipped by the public disable()/enable()
   * API or the `data-bug-fab-disabled` script attribute. `anchorEl` and the
   * observers below back the stackAbove/stackBelow/stackLeft/stackRight
   * anchoring mode (TH-6).
   */
  let userDisabled = false;
  let anchorEl = null;
  let anchorMode = null; // "above" | "below" | "left" | "right"
  let anchorResizeHandler = null;
  let anchorIntersectionObserver = null;
  let anchorMutationObserver = null;

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

  // Annotation tools (TH-14) — additional state for the tool palette.
  // `activeTool` is one of "draw" (free-draw, default), "rectangle",
  // "arrow", "blur", "eraser", "text". `toolStartX/Y` records the start
  // of a click-and-drag stroke for shape tools. `preStrokeSnapshot` is
  // an ImageData captured at mousedown; shape previews redraw it on each
  // mousemove so the in-progress shape doesn't leave a trail. `undoStack`
  // holds full-canvas ImageData snapshots, one per committed stroke.
  // `pendingTextInput` is the live <input> element when the text tool
  // is mid-entry. The toolbar buttons are looked up via `toolButtonEls`.
  let activeTool = "draw";
  let toolStartX = 0;
  let toolStartY = 0;
  let preStrokeSnapshot = null;
  const undoStack = [];
  const UNDO_STACK_LIMIT = 30;
  let pendingTextInput = null;
  let toolButtonEls = null;

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
      /* Default offsets are applied via inline style at FAB-element
         creation time (FAB UX TH-5). Caller can override via
         BugFab.init({ position: ... }) or anchor it to another element
         via stackAbove/stackBelow/stackLeft/stackRight (TH-6). */
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
    /* Mobile/tablet: bigger touch target. Bottom-lift only applies when
       no caller-customized position is in effect (FAB UX TH-5). */
    @media (max-width: 899px) {
      .bug-fab {
        width: 64px;
        height: 64px;
      }
    }
    .bug-fab--hidden { display: none !important; }

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
      /* Annotation tools (TH-14): toolbar (~46px) + clear-btn (~56px) */
      max-height: calc(100% - 110px);
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
    /* Annotation tools (TH-14) — tool palette / toolbar above canvas */
    .bug-fab-toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 6px 8px;
      margin-bottom: 8px;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 4px;
      align-items: center;
      max-width: 100%;
    }
    .bug-fab-tool {
      min-width: 36px;
      height: 32px;
      padding: 0 8px;
      border: 1px solid rgba(255, 255, 255, 0.35);
      background: rgba(255, 255, 255, 0.04);
      color: #fff;
      border-radius: 4px;
      cursor: pointer;
      font-family: inherit;
      font-size: 0.8125rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
    }
    .bug-fab-tool:hover:not(:disabled) {
      background: rgba(255, 255, 255, 0.14);
    }
    .bug-fab-tool[aria-pressed="true"] {
      background: #f44336;
      border-color: #f44336;
      color: #fff;
    }
    .bug-fab-tool:focus-visible {
      outline: 2px solid #fff;
      outline-offset: 1px;
    }
    .bug-fab-toolbar__sep {
      width: 1px;
      align-self: stretch;
      background: rgba(255, 255, 255, 0.25);
      margin: 0 2px;
    }
    .bug-fab-toolbar__help {
      margin-left: auto;
      color: rgba(255, 255, 255, 0.7);
      font-size: 0.75rem;
    }
    .bug-fab-canvas-text-input {
      position: absolute;
      z-index: 10000;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      font-size: 18px;
      padding: 2px 4px;
      background: rgba(255, 255, 255, 0.95);
      color: #212529;
      border: 1px dashed #f44336;
      border-radius: 2px;
      outline: none;
      min-width: 80px;
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

  /**
   * Should the FAB be visible right now?
   *
   * Resolves three independent gates:
   *
   *   1. `userDisabled` — runtime kill-switch flipped by `BugFab.disable()`
   *      or by a `data-bug-fab-disabled="true"` attribute on the bundle's
   *      <script> tag (FAB UX TH-7). Wins over everything else.
   *   2. `config.enabled` — boolean OR () => boolean. `false` and a
   *      callable returning falsy hide the FAB; `true` and a callable
   *      returning truthy show it. `null`/`undefined` means "default-on".
   *      The literal-boolean branch is honored BEFORE the function-call
   *      branch — `enabled: false` was previously a silent no-op because
   *      `isEnabled()` only treated `enabled` as a gate when it was a
   *      callable. Surfaced by a 2026-05-03 consumer-integration audit.
   *   3. Default-on.
   *
   * @returns {boolean} whether the FAB should currently render.
   */
  const isEnabled = () => {
    if (userDisabled) return false;
    if (config.enabled === false) return false;
    if (config.enabled === true) return true;
    if (typeof config.enabled === "function") {
      try { return Boolean(config.enabled()); } catch (_e) { return false; }
    }
    return true;
  };

  /**
   * Read the bundle's <script> tag for `data-bug-fab-disabled="true"` so
   * non-JS templates can flip the kill-switch without rebuilding config.
   * Uses document.currentScript when available (during initial parse) and
   * falls back to a getElementsByTagName scan for late-loaded contexts.
   */
  const readScriptDisabledAttr = () => {
    let scriptEl = document.currentScript;
    if (!scriptEl) {
      const scripts = document.getElementsByTagName("script");
      for (let i = scripts.length - 1; i >= 0; i--) {
        const src = scripts[i].src || "";
        if (src.includes("bug-fab.js")) { scriptEl = scripts[i]; break; }
      }
    }
    if (!scriptEl) return false;
    const v = scriptEl.getAttribute("data-bug-fab-disabled");
    return v === "true" || v === "";
  };

  /**
   * Read the bundle's <script> tag for `data-submit-url="..."` so the
   * "drop in a single <script> tag" Quickstart actually works without
   * an explicit BugFab.init({ submitUrl }) call. Surfaced by the
   * 2026-05-03 post-e2e consumer audit (TH-Critical): without this
   * fallback, the FAB renders, the user clicks it, and nothing
   * visible happens — silent failure mode.
   */
  const readScriptSubmitUrlAttr = () => {
    let scriptEl = document.currentScript;
    if (!scriptEl) {
      const scripts = document.getElementsByTagName("script");
      for (let i = scripts.length - 1; i >= 0; i--) {
        const src = scripts[i].src || "";
        if (src.includes("bug-fab.js")) { scriptEl = scripts[i]; break; }
      }
    }
    if (!scriptEl) return null;
    const v = scriptEl.getAttribute("data-submit-url");
    return v || null;
  };

  /** Cached at script-load: the bundle's own <script> data-* state. */
  const SCRIPT_DISABLED_AT_LOAD = readScriptDisabledAttr();
  const SCRIPT_SUBMIT_URL_AT_LOAD = readScriptSubmitUrlAttr();

  /**
   * Default intake path applied when neither explicit init config nor
   * a `data-submit-url` script attribute supplies one. Matches the
   * canonical FastAPI mount documented in `docs/INSTALLATION.md`
   * (the `submit_router` mounted under `/api`). Consumers using a
   * different prefix override this via `BugFab.init({ submitUrl: ... })`
   * or `<script src="..." data-submit-url="...">`.
   */
  const DEFAULT_SUBMIT_URL = "/api/bug-reports";

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
  // FAB positioning (TH-5 corners + free-form, TH-6 anchor-to-element)
  // ====================================================================

  /** CSS offsets for the four supported corner keywords. */
  const CORNER_OFFSETS = Object.freeze({
    "bottom-right": { bottom: "24px", right: "24px" },
    "bottom-left": { bottom: "24px", left: "24px" },
    "top-right": { top: "24px", right: "24px" },
    "top-left": { top: "24px", left: "24px" },
  });

  /**
   * Resolve `config.position` (string keyword OR free-form
   * {top,bottom,left,right} object) into a { top, bottom, left, right }
   * object whose values are CSS strings or null. Unknown keywords fall
   * back to bottom-right.
   */
  const resolvePositionOffsets = () => {
    const pos = config.position;
    if (pos && typeof pos === "object") {
      return {
        top: pos.top != null ? String(pos.top) : null,
        bottom: pos.bottom != null ? String(pos.bottom) : null,
        left: pos.left != null ? String(pos.left) : null,
        right: pos.right != null ? String(pos.right) : null,
      };
    }
    const key = typeof pos === "string" ? pos : "bottom-right";
    const corner = CORNER_OFFSETS[key] || CORNER_OFFSETS["bottom-right"];
    return {
      top: corner.top || null,
      bottom: corner.bottom || null,
      left: corner.left || null,
      right: corner.right || null,
    };
  };

  /** Apply a {top,bottom,left,right} offset object to the FAB. */
  const applyFabOffsets = (offsets) => {
    if (!fab) return;
    fab.style.top = offsets.top || "";
    fab.style.bottom = offsets.bottom || "";
    fab.style.left = offsets.left || "";
    fab.style.right = offsets.right || "";
  };

  /** Resolve a selector-or-element ref into an HTMLElement, or null. */
  const resolveAnchorRef = (ref) => {
    if (!ref) return null;
    if (ref instanceof HTMLElement) return ref;
    if (typeof ref === "string") {
      try { return document.querySelector(ref); } catch (_e) { return null; }
    }
    return null;
  };

  /** Pick the active anchor mode + ref from config, or [null, null]. */
  const pickAnchorConfig = () => {
    if (config.stackAbove) return ["above", config.stackAbove];
    if (config.stackBelow) return ["below", config.stackBelow];
    if (config.stackLeft) return ["left", config.stackLeft];
    if (config.stackRight) return ["right", config.stackRight];
    return [null, null];
  };

  /**
   * Recompute the FAB's offsets based on the anchor element's bounding
   * rect. Called on init, on window resize, and via observers when the
   * anchor's geometry changes.
   */
  const repositionToAnchor = () => {
    if (!fab || !anchorEl || !anchorMode) return;
    const rect = anchorEl.getBoundingClientRect();
    const gap = Math.max(0, Number(config.gap) || 0);
    const fabW = fab.offsetWidth || 56;
    const fabH = fab.offsetHeight || 56;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let top = null;
    let left = null;
    if (anchorMode === "above") {
      top = `${Math.max(0, rect.top - fabH - gap)}px`;
      left = `${Math.max(0, rect.left + (rect.width - fabW) / 2)}px`;
    } else if (anchorMode === "below") {
      top = `${Math.min(vh - fabH, rect.bottom + gap)}px`;
      left = `${Math.max(0, rect.left + (rect.width - fabW) / 2)}px`;
    } else if (anchorMode === "left") {
      top = `${Math.max(0, rect.top + (rect.height - fabH) / 2)}px`;
      left = `${Math.max(0, rect.left - fabW - gap)}px`;
    } else if (anchorMode === "right") {
      top = `${Math.max(0, rect.top + (rect.height - fabH) / 2)}px`;
      left = `${Math.min(vw - fabW, rect.right + gap)}px`;
    }
    applyFabOffsets({ top, bottom: null, left, right: null });
  };

  /** Wire up resize + intersection + mutation observers for the anchor. */
  const installAnchorObservers = () => {
    if (!anchorEl) return;
    anchorResizeHandler = () => repositionToAnchor();
    window.addEventListener("resize", anchorResizeHandler);
    if (typeof IntersectionObserver === "function") {
      anchorIntersectionObserver = new IntersectionObserver(() => {
        repositionToAnchor();
      });
      anchorIntersectionObserver.observe(anchorEl);
    }
    if (typeof MutationObserver === "function") {
      anchorMutationObserver = new MutationObserver(() => {
        repositionToAnchor();
      });
      // Watch the anchor for class/style changes that might shift it.
      anchorMutationObserver.observe(anchorEl, {
        attributes: true,
        attributeFilter: ["class", "style"],
      });
    }
  };

  /** Tear down anchor observers (called by destroy()). */
  const teardownAnchorObservers = () => {
    if (anchorResizeHandler) {
      window.removeEventListener("resize", anchorResizeHandler);
      anchorResizeHandler = null;
    }
    if (anchorIntersectionObserver) {
      anchorIntersectionObserver.disconnect();
      anchorIntersectionObserver = null;
    }
    if (anchorMutationObserver) {
      anchorMutationObserver.disconnect();
      anchorMutationObserver = null;
    }
    anchorEl = null;
    anchorMode = null;
  };

  /**
   * Apply position to the FAB based on config: anchor first, fall back to
   * `position` if the anchor selector doesn't resolve.
   */
  const applyConfiguredPosition = () => {
    if (!fab) return;
    const [mode, ref] = pickAnchorConfig();
    if (mode) {
      const el = resolveAnchorRef(ref);
      if (el) {
        anchorMode = mode;
        anchorEl = el;
        installAnchorObservers();
        repositionToAnchor();
        return;
      }
      console.warn(
        `Bug-Fab: stack${mode.charAt(0).toUpperCase() + mode.slice(1)} ` +
          `anchor not found; falling back to position config.`
      );
    }
    applyFabOffsets(resolvePositionOffsets());
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

    // FAB UX (TH-5/6): apply configured position now that the element is
    // in the DOM (offsetWidth/offsetHeight need a layout pass).
    applyConfiguredPosition();
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
  //
  // Annotation tools (TH-14) — the canvas section below was extended to
  // support a small tool palette: free-draw (default, the v0.1 behavior),
  // eraser, rectangle, arrow, blur (privacy redact), and text label,
  // plus an undo stack and keyboard shortcuts.
  //
  // Design notes:
  //   - One snapshot per committed stroke is pushed onto `undoStack` at
  //     mousedown (BEFORE the stroke renders), so undo restores the
  //     pre-stroke state. Every tool's commit path opts into the same
  //     model — uniform undo, no per-tool branching for history.
  //   - Shape tools (rectangle, arrow, blur) preview by re-blitting the
  //     `preStrokeSnapshot` ImageData on each mousemove and drawing the
  //     in-progress shape on top. On mouseup the in-progress shape is
  //     committed by drawing it once more on the snapshot.
  //   - Blur uses `ctx.filter = 'blur(12px)'`. Both Chromium 88+ and
  //     Firefox 103+ support canvas filters (Safari 17+ also). For the
  //     POC and shipped browser test target this is fine. Adapter authors
  //     who need older-browser fallback can polyfill via a downscale +
  //     upscale pixelation trick (out of scope for now).
  //   - Text labels are entered via a transient absolute-positioned
  //     <input> over the canvas — lets the user use IME, clipboard,
  //     RTL input, and a11y tools naturally. Committed on Enter/blur,
  //     cancelled on Escape, then rendered with `ctx.fillText` and a
  //     soft black drop shadow so the glyph stays readable on busy
  //     backgrounds.

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

  // ----- Annotation tools (TH-14): undo stack helpers -----------------

  /** Snapshot the current canvas pixels onto the undo stack. */
  const pushUndoSnapshot = () => {
    if (!canvasCtx || !annotationCanvasEl) return;
    try {
      const snap = canvasCtx.getImageData(
        0, 0, annotationCanvasEl.width, annotationCanvasEl.height
      );
      undoStack.push(snap);
      while (undoStack.length > UNDO_STACK_LIMIT) undoStack.shift();
    } catch (_e) {
      // getImageData can throw on a tainted canvas (cross-origin image
      // without CORS). html2canvas keeps the canvas same-origin so this
      // is unexpected, but swallow defensively rather than crashing the
      // overlay — undo just becomes a no-op for that session.
    }
  };

  /** Pop the latest snapshot and re-paint it. */
  const undoLastStroke = () => {
    if (!canvasCtx || !annotationCanvasEl) return;
    if (undoStack.length === 0) return;
    const snap = undoStack.pop();
    canvasCtx.putImageData(snap, 0, 0);
  };

  // ----- Annotation tools (TH-14): per-tool commit helpers ------------

  /** Resolve the live stroke color from config, falling back to v0.1 red. */
  const getStrokeColor = () =>
    (config && config.annotationColor) || "#f44336";

  /** Free-draw stroke segment. Used by both `draw` and `eraser` tools. */
  const drawFreeSegment = (pos) => {
    if (!canvasCtx) return;
    canvasCtx.save();
    if (activeTool === "eraser") {
      // Eraser paints the pristine screenshot back through a clipped
      // circle. Using destination-out would cut a hole through to
      // transparency, which would composite badly when toBlob() flattens
      // to PNG; re-drawing the pristine screenshot inside a circular
      // clip keeps the output an opaque PNG.
      canvasCtx.beginPath();
      canvasCtx.arc(pos.x, pos.y, 14, 0, Math.PI * 2);
      canvasCtx.clip();
      if (screenshotImage) {
        canvasCtx.drawImage(
          screenshotImage, 0, 0,
          annotationCanvasEl.width, annotationCanvasEl.height
        );
      }
    } else {
      canvasCtx.strokeStyle = getStrokeColor();
      canvasCtx.lineWidth = 3;
      canvasCtx.lineCap = "round";
      canvasCtx.lineJoin = "round";
      canvasCtx.beginPath();
      canvasCtx.moveTo(lastX, lastY);
      canvasCtx.lineTo(pos.x, pos.y);
      canvasCtx.stroke();
    }
    canvasCtx.restore();
    lastX = pos.x;
    lastY = pos.y;
  };

  /** Re-paint the snapshot taken at mousedown (shape-tool preview). */
  const restorePreStrokeSnapshot = () => {
    if (!canvasCtx || !preStrokeSnapshot) return;
    canvasCtx.putImageData(preStrokeSnapshot, 0, 0);
  };

  /** Draw a rectangle outline from (toolStartX/Y) to (pos.x/y). */
  const drawRectOutline = (pos) => {
    if (!canvasCtx) return;
    canvasCtx.save();
    canvasCtx.strokeStyle = getStrokeColor();
    canvasCtx.lineWidth = 3;
    canvasCtx.lineJoin = "miter";
    canvasCtx.strokeRect(
      toolStartX, toolStartY,
      pos.x - toolStartX, pos.y - toolStartY
    );
    canvasCtx.restore();
  };

  /** Draw a line + arrowhead from (toolStartX/Y) to (pos.x/y). */
  const drawArrow = (pos) => {
    if (!canvasCtx) return;
    const dx = pos.x - toolStartX;
    const dy = pos.y - toolStartY;
    const len = Math.hypot(dx, dy);
    if (len < 1) return; // ignore tiny clicks
    canvasCtx.save();
    canvasCtx.strokeStyle = getStrokeColor();
    canvasCtx.fillStyle = getStrokeColor();
    canvasCtx.lineWidth = 3;
    canvasCtx.lineCap = "round";
    canvasCtx.lineJoin = "round";
    canvasCtx.beginPath();
    canvasCtx.moveTo(toolStartX, toolStartY);
    canvasCtx.lineTo(pos.x, pos.y);
    canvasCtx.stroke();
    // Arrowhead: two short lines from the end point at +/- 30° back
    // from the direction the line was drawn in.
    const headLen = Math.min(18, len * 0.4);
    const angle = Math.atan2(dy, dx);
    const wing = Math.PI / 6; // 30°
    canvasCtx.beginPath();
    canvasCtx.moveTo(pos.x, pos.y);
    canvasCtx.lineTo(
      pos.x - headLen * Math.cos(angle - wing),
      pos.y - headLen * Math.sin(angle - wing)
    );
    canvasCtx.moveTo(pos.x, pos.y);
    canvasCtx.lineTo(
      pos.x - headLen * Math.cos(angle + wing),
      pos.y - headLen * Math.sin(angle + wing)
    );
    canvasCtx.stroke();
    canvasCtx.restore();
  };

  /** Preview rectangle for the blur tool — dashed outline, no fill yet. */
  const drawBlurPreview = (pos) => {
    if (!canvasCtx) return;
    canvasCtx.save();
    canvasCtx.strokeStyle = "rgba(0, 0, 0, 0.7)";
    canvasCtx.lineWidth = 1;
    canvasCtx.setLineDash([6, 4]);
    canvasCtx.strokeRect(
      toolStartX, toolStartY,
      pos.x - toolStartX, pos.y - toolStartY
    );
    canvasCtx.restore();
  };

  /**
   * Commit a blur over the rectangle defined by (toolStartX/Y, pos).
   * Routes through a scratch canvas because drawing a canvas onto itself
   * with `filter` set is unreliable across browsers.
   */
  const commitBlurRect = (pos) => {
    if (!canvasCtx || !annotationCanvasEl) return;
    const x = Math.min(toolStartX, pos.x);
    const y = Math.min(toolStartY, pos.y);
    const w = Math.abs(pos.x - toolStartX);
    const h = Math.abs(pos.y - toolStartY);
    if (w < 4 || h < 4) return; // ignore stray clicks

    const scratch = document.createElement("canvas");
    scratch.width = annotationCanvasEl.width;
    scratch.height = annotationCanvasEl.height;
    const scratchCtx = scratch.getContext("2d");
    if (!scratchCtx) return;
    if (preStrokeSnapshot) {
      scratchCtx.putImageData(preStrokeSnapshot, 0, 0);
    } else {
      scratchCtx.drawImage(annotationCanvasEl, 0, 0);
    }

    canvasCtx.save();
    canvasCtx.beginPath();
    canvasCtx.rect(x, y, w, h);
    canvasCtx.clip();
    canvasCtx.filter = "blur(12px)";
    canvasCtx.drawImage(scratch, 0, 0);
    canvasCtx.filter = "none";
    canvasCtx.restore();
  };

  /** Render a single text-label glyph at the recorded toolStart position. */
  const commitTextLabel = (text) => {
    if (!canvasCtx) return;
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    canvasCtx.save();
    canvasCtx.font = "18px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    canvasCtx.textBaseline = "top";
    canvasCtx.shadowColor = "rgba(0, 0, 0, 0.6)";
    canvasCtx.shadowBlur = 4;
    canvasCtx.shadowOffsetX = 1;
    canvasCtx.shadowOffsetY = 1;
    canvasCtx.fillStyle = getStrokeColor();
    canvasCtx.fillText(trimmed, toolStartX, toolStartY);
    canvasCtx.restore();
  };

  /** Float a transient input over the canvas at the given client coords. */
  const openTextInputAt = (clientX, clientY, canvasPos) => {
    if (!annotationCanvasEl || !annotationCanvasEl.parentNode) return;
    closePendingTextInput(false);
    toolStartX = canvasPos.x;
    toolStartY = canvasPos.y;
    const wrap = annotationCanvasEl.parentNode;
    const wrapRect = wrap.getBoundingClientRect();
    const inputEl = document.createElement("input");
    inputEl.type = "text";
    inputEl.className = "bug-fab-canvas-text-input";
    inputEl.style.left = (clientX - wrapRect.left) + "px";
    inputEl.style.top = (clientY - wrapRect.top) + "px";
    inputEl.setAttribute("aria-label", "Text annotation");
    inputEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        closePendingTextInput(true);
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        closePendingTextInput(false);
      }
      ev.stopPropagation();
    });
    inputEl.addEventListener("blur", () => closePendingTextInput(true));
    wrap.appendChild(inputEl);
    pendingTextInput = inputEl;
    setTimeout(() => inputEl.focus(), 0);
  };

  /** Tear down the floating text input; commit the value if `commit`. */
  const closePendingTextInput = (commit) => {
    if (!pendingTextInput) return;
    const value = pendingTextInput.value;
    const el = pendingTextInput;
    pendingTextInput = null;
    if (el.parentNode) el.parentNode.removeChild(el);
    if (commit && value) {
      pushUndoSnapshot();
      commitTextLabel(value);
    }
  };

  // ----- Annotation tools (TH-14): pointer-event dispatch -------------

  const startDraw = (pos) => {
    isDrawing = true;
    lastX = pos.x;
    lastY = pos.y;
    toolStartX = pos.x;
    toolStartY = pos.y;
    pushUndoSnapshot();
    if (
      activeTool === "rectangle" ||
      activeTool === "arrow" ||
      activeTool === "blur"
    ) {
      try {
        preStrokeSnapshot = canvasCtx.getImageData(
          0, 0, annotationCanvasEl.width, annotationCanvasEl.height
        );
      } catch (_e) {
        preStrokeSnapshot = null;
      }
    }
  };

  const continueDraw = (pos) => {
    if (!isDrawing || !canvasCtx) return;
    if (activeTool === "draw" || activeTool === "eraser") {
      drawFreeSegment(pos);
      return;
    }
    // Shape tools — preview by restoring snapshot then drawing on top.
    restorePreStrokeSnapshot();
    if (activeTool === "rectangle") drawRectOutline(pos);
    else if (activeTool === "arrow") drawArrow(pos);
    else if (activeTool === "blur") drawBlurPreview(pos);
  };

  const stopDraw = (pos) => {
    if (!isDrawing) return;
    isDrawing = false;
    if (canvasCtx && pos) {
      // Commit the final shape on mouseup.
      if (activeTool === "rectangle") {
        restorePreStrokeSnapshot();
        drawRectOutline(pos);
      } else if (activeTool === "arrow") {
        restorePreStrokeSnapshot();
        drawArrow(pos);
      } else if (activeTool === "blur") {
        restorePreStrokeSnapshot();
        commitBlurRect(pos);
      }
    }
    preStrokeSnapshot = null;
  };

  const onMouseDown = (e) => {
    e.preventDefault();
    if (activeTool === "text") {
      const pos = getMousePos(e);
      openTextInputAt(e.clientX, e.clientY, pos);
      return;
    }
    startDraw(getMousePos(e));
  };
  const onMouseMove = (e) => {
    if (!isDrawing) return;
    e.preventDefault();
    continueDraw(getMousePos(e));
  };
  const onMouseUp = (e) => {
    e.preventDefault();
    stopDraw(isDrawing ? getMousePos(e) : null);
  };
  const onTouchStart = (e) => {
    e.preventDefault();
    if (e.touches.length !== 1) return;
    if (activeTool === "text") {
      const t = e.touches[0];
      const pos = getTouchPos(e);
      openTextInputAt(t.clientX, t.clientY, pos);
      return;
    }
    startDraw(getTouchPos(e));
  };
  const onTouchMove = (e) => {
    e.preventDefault();
    if (!isDrawing || e.touches.length !== 1) return;
    continueDraw(getTouchPos(e));
  };
  const onTouchEnd = (e) => {
    e.preventDefault();
    stopDraw(isDrawing ? getTouchPos(e) : null);
  };

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

  // ----- Annotation tools (TH-14): tool palette + cursor + shortcuts --

  /** Map of single-key shortcuts (lowercased) to tool names. */
  const TOOL_SHORTCUTS = Object.freeze({
    d: "draw",
    r: "rectangle",
    a: "arrow",
    b: "blur",
    t: "text",
    e: "eraser",
  });

  /** CSS cursor per active tool. */
  const cursorForTool = (tool) => {
    if (tool === "text") return "text";
    if (tool === "eraser") return "cell";
    // draw / rectangle / arrow / blur all share the crosshair cursor.
    return "crosshair";
  };

  /** Switch the active tool and update toolbar + canvas cursor state. */
  const setActiveTool = (tool) => {
    // Drop any in-flight text input when switching tools.
    closePendingTextInput(false);
    activeTool = tool;
    if (annotationCanvasEl) {
      annotationCanvasEl.style.cursor = cursorForTool(tool);
    }
    if (toolButtonEls) {
      for (const btn of toolButtonEls) {
        const isActive = btn.getAttribute("data-bug-fab-tool") === tool;
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      }
    }
  };

  /** Toolbar HTML. Plain text labels — no font-icon CDN dependency. */
  const buildToolbarHtml = () => `
    <div class="bug-fab-toolbar" role="toolbar" aria-label="Annotation tools"
         data-bug-fab-toolbar>
      <button type="button" class="bug-fab-tool" data-bug-fab-tool="draw"
        aria-pressed="true" aria-label="Free draw (d)" title="Free draw (d)">Draw</button>
      <button type="button" class="bug-fab-tool" data-bug-fab-tool="rectangle"
        aria-pressed="false" aria-label="Rectangle (r)" title="Rectangle (r)">Rect</button>
      <button type="button" class="bug-fab-tool" data-bug-fab-tool="arrow"
        aria-pressed="false" aria-label="Arrow (a)" title="Arrow (a)">Arrow</button>
      <button type="button" class="bug-fab-tool" data-bug-fab-tool="blur"
        aria-pressed="false" aria-label="Blur / privacy redact (b)" title="Blur (b)">Blur</button>
      <button type="button" class="bug-fab-tool" data-bug-fab-tool="text"
        aria-pressed="false" aria-label="Text label (t)" title="Text (t)">Text</button>
      <span class="bug-fab-toolbar__sep" aria-hidden="true"></span>
      <button type="button" class="bug-fab-tool" data-bug-fab-tool="eraser"
        aria-pressed="false" aria-label="Eraser (e)" title="Eraser (e)">Erase</button>
      <button type="button" class="bug-fab-tool" data-bug-fab-undo
        aria-label="Undo (u or Ctrl+Z)" title="Undo (u / Ctrl+Z)">Undo</button>
      <span class="bug-fab-toolbar__help"
        title="d=draw, r=rect, a=arrow, b=blur, t=text, e=erase, u=undo (Ctrl+Z also undoes)">?</span>
    </div>
  `;

  /** Wire toolbar button handlers. */
  const bindToolbar = (toolbarEl) => {
    if (!toolbarEl) return;
    toolButtonEls = toolbarEl.querySelectorAll("[data-bug-fab-tool]");
    for (const btn of toolButtonEls) {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const tool = btn.getAttribute("data-bug-fab-tool");
        if (tool) setActiveTool(tool);
      });
    }
    const undoBtn = toolbarEl.querySelector("[data-bug-fab-undo]");
    if (undoBtn) {
      undoBtn.addEventListener("click", (e) => {
        e.preventDefault();
        undoLastStroke();
      });
    }
  };

  /** Document-level keyboard shortcut handler installed with the overlay. */
  const onAnnotationKey = (e) => {
    // Ignore keystrokes when the user is typing in an input/textarea/select
    // — including the floating text-label input (which sets pendingTextInput).
    const target = e.target;
    const tag = target && target.tagName ? target.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (pendingTextInput) return;
    if ((e.ctrlKey || e.metaKey) && (e.key || "").toLowerCase() === "z") {
      e.preventDefault();
      undoLastStroke();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = (e.key || "").toLowerCase();
    if (k === "u") { e.preventDefault(); undoLastStroke(); return; }
    if (Object.prototype.hasOwnProperty.call(TOOL_SHORTCUTS, k)) {
      e.preventDefault();
      setActiveTool(TOOL_SHORTCUTS[k]);
    }
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
    // Annotation tools (TH-14): reset tool state for each fresh open.
    undoStack.length = 0;
    activeTool = "draw";
    preStrokeSnapshot = null;

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
      annotationCanvasEl.style.cursor = cursorForTool(activeTool);
    };

    screenshotImage.onload = onReady;
    if (screenshotImage.complete && screenshotImage.naturalWidth > 0) onReady();
  };

  /** Wipe annotations, restoring the original screenshot. */
  const clearAnnotations = () => {
    if (!canvasCtx || !screenshotImage) return;
    closePendingTextInput(false);
    pushUndoSnapshot();
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
      // Annotation tools (TH-14): commit any in-flight text input before
      // toBlob runs, so the user doesn't lose typed-but-not-yet-committed
      // labels by clicking submit.
      closePendingTextInput(true);
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

  /**
   * Build the category-dropdown field HTML for the report form.
   * FAB UX (TH-15) — coordinate with annotation subagent: this slots
   * between the title and description fields. Returns "" when categories
   * is unset, preserving the original form layout for back-compat.
   */
  const buildCategoryFieldHtml = () => {
    const cats = config.categories;
    if (!Array.isArray(cats) || cats.length === 0) return "";
    const label = escapeHtml(config.categoryLabel || "Category");
    let opts = `<option value="">--</option>`;
    for (const c of cats) {
      const v = escapeHtml(c);
      opts += `<option value="${v}">${v}</option>`;
    }
    return `
            <div class="bug-fab-field">
              <label for="bug-fab-category">${label}</label>
              <select id="bug-fab-category" name="category" class="bug-fab-select"
                data-bug-fab-category>
                ${opts}
              </select>
            </div>`;
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
          ${buildToolbarHtml()}
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
            ${buildCategoryFieldHtml()}
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

    // FAB UX (TH-15): if a category dropdown is rendered, prepend the
    // chosen value to the tags array (only when a non-empty option is
    // selected — the placeholder maps to "").
    const userTags = parseTags(overlay.querySelector("#bug-fab-tags").value);
    const categorySel = overlay.querySelector("[data-bug-fab-category]");
    const categoryValue = categorySel ? (categorySel.value || "").trim() : "";
    const tags = categoryValue ? [categoryValue, ...userTags] : userTags;

    const metadata = {
      protocol_version: "0.1",
      title,
      client_ts: new Date().toISOString(),
      report_type: overlay.querySelector("#bug-fab-type").value,
      description: overlay.querySelector("#bug-fab-description").value.trim(),
      expected_behavior: overlay.querySelector("#bug-fab-expected").value.trim(),
      severity: overlay.querySelector("#bug-fab-severity").value,
      tags,
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

    // Annotation tools (TH-14): wire toolbar buttons + keyboard shortcuts.
    bindToolbar(overlay.querySelector("[data-bug-fab-toolbar]"));
    document.addEventListener("keydown", onAnnotationKey);

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
    // Annotation tools (TH-14): unbind tool key handler + reset state.
    document.removeEventListener("keydown", onAnnotationKey);
    closePendingTextInput(false);
    toolButtonEls = null;
    undoStack.length = 0;
    activeTool = "draw";
    preStrokeSnapshot = null;
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

    // TH-Critical (post-e2e): `submitUrl: null` after merge is the
    // single biggest first-time-integrator footgun. Resolve a working
    // default in priority order:
    //   1. Explicit init: userConfig.submitUrl wins (already merged above).
    //   2. <script src="..." data-submit-url="..."> on the bundle tag.
    //   3. Hard-coded /api/bug-reports default (canonical FastAPI mount).
    // Consumers who mount under a non-canonical prefix override via 1 or 2.
    if (!config.submitUrl) {
      config.submitUrl = SCRIPT_SUBMIT_URL_AT_LOAD || DEFAULT_SUBMIT_URL;
    }

    // FAB UX (TH-7): honor `data-bug-fab-disabled="true"` on the bundle's
    // own <script> tag so non-JS templates have a kill-switch without
    // rebuilding the init config.
    if (SCRIPT_DISABLED_AT_LOAD) userDisabled = true;

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
   * FAB UX (TH-7): hide the FAB at runtime. If the overlay is currently
   * open it's closed first so the user can't continue editing a report
   * while the host considers Bug-Fab disabled. Idempotent.
   */
  const disable = () => {
    userDisabled = true;
    if (overlay) closeOverlay();
    if (fab) fab.classList.add("bug-fab--hidden");
  };

  /**
   * FAB UX (TH-7): re-show the FAB at runtime. Idempotent. If init() has
   * run but the FAB was never created (because `enabled` was false or the
   * script-tag kill-switch was set), this lazily creates it so a host's
   * `init()` + later `enable()` flow works.
   */
  const enable = () => {
    userDisabled = false;
    if (!initialized) return;
    if (!fab) {
      injectStyles();
      createFab();
      startBadgePolling();
    } else {
      fab.classList.remove("bug-fab--hidden");
    }
  };

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
    teardownAnchorObservers();
    if (fab && fab.parentNode) fab.parentNode.removeChild(fab);
    fab = null;
    badge = null;
    closeOverlay();
    if (__origFetch) window.fetch = __origFetch;
    userDisabled = false;
    initialized = false;
  };

  window.BugFab = Object.freeze({
    init,
    open,
    disable,
    enable,
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
