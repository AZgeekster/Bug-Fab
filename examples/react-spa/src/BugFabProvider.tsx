/**
 * BugFabProvider — React wrapper around the Bug-Fab vanilla JS bundle.
 *
 * Bug-Fab v0.1 ships as a vanilla-JS bundle (no npm package yet — that lands
 * in v0.2). This provider gives React/SPA consumers a clean integration story:
 * inject the bundle script once, call `window.BugFab.init(config)` on mount,
 * tear it down on unmount.
 *
 * Usage:
 *   <BugFabProvider config={{ submitUrl: "/api/bug-reports" }}>
 *     <App />
 *   </BugFabProvider>
 *
 * Anywhere inside the tree:
 *   const { open, version } = useBugFab();
 *   <button onClick={open}>Report a bug</button>
 *
 * The FAB is also auto-rendered by the bundle on init, so the hook is only
 * needed for *programmatic* opens (e.g., a menu item or keyboard shortcut).
 */

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------------------
// Types — mirror the bundle's config schema (see repo/static/bug-fab.js).
// ---------------------------------------------------------------------------

/** Pathname-prefix to label map used by the bundle's module detection. */
export type BugFabModuleMap = Record<string, string>;

/** Server's response body to a successful POST /api/bug-reports. */
export interface BugFabSubmitResult {
  id: string;
  received_at: string;
  stored_at: string;
  github_issue_url: string | null;
}

/**
 * Bug-Fab init config. All keys map 1:1 to the vanilla bundle's config
 * (see repo/static/README.md § config). `bundlePath` is provider-only;
 * everything else is forwarded verbatim into `window.BugFab.init()`.
 */
export interface BugFabConfig {
  /**
   * URL the provider script-injects to load `bug-fab.js`.
   * Default: `/bug-fab/bug-fab.js`.
   *
   * Most consumers either (a) proxy this path to the bundle's host backend,
   * or (b) copy the bundle into their own `public/` directory.
   */
  bundlePath?: string;

  /** **Required.** Endpoint that accepts the multipart POST. */
  submitUrl: string;

  /** Extra request headers (e.g., CSRF, auth). Object or () => object. */
  headers?: Record<string, string> | (() => Record<string, string>);

  /** Predicate gating FAB visibility. Default: always-on. */
  enabled?: () => boolean;

  /** Pathname-prefix to module-label map for `context.module`. */
  moduleMap?: BugFabModuleMap;

  /** Filter for which URLs land in the network log. */
  networkUrlPattern?: RegExp;

  /** Surfaced as `context.app_version` in the report. */
  appVersion?: string;

  /** Surfaced as `context.environment` in the report. */
  environment?: string;

  /** FAB cooldown after a successful submit. Default: 30s. */
  cooldownSeconds?: number;

  /** Cap on the error + network buffers. Default: 50. */
  bufferSize?: number;

  /** Optional callback after a successful POST. */
  onSubmitSuccess?: (report: BugFabSubmitResult | null) => void;

  /** Optional callback when the POST fails. */
  onSubmitError?: (error: Error) => void;

  /** Override where to fetch html2canvas. */
  html2canvasUrl?: string;
}

/** Shape exposed on `window` by the loaded bundle. */
interface BugFabGlobal {
  init: (config: Omit<BugFabConfig, "bundlePath">) => void;
  open: () => void;
  destroy: () => void;
  version: string;
}

declare global {
  interface Window {
    BugFab?: BugFabGlobal;
    BugFabAutoInit?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface BugFabContextValue {
  /** Programmatically open the bug-report overlay. No-op until ready. */
  open: () => void;
  /** Bundle version string (empty until the bundle has loaded). */
  version: string;
  /** True once `window.BugFab.init()` has run successfully. */
  ready: boolean;
}

const BugFabContext = createContext<BugFabContextValue | null>(null);

/**
 * Hook returning the programmatic Bug-Fab API.
 *
 * Must be called inside a `<BugFabProvider>` subtree. Returns a stable
 * `open` function and the bundle's `version` string. Calls to `open()`
 * before the bundle has finished loading are silently ignored.
 */
export function useBugFab(): BugFabContextValue {
  const ctx = useContext(BugFabContext);
  if (!ctx) {
    throw new Error("useBugFab must be used inside a <BugFabProvider>.");
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// Module-scoped script-load tracking
// ---------------------------------------------------------------------------
//
// React 18 StrictMode mounts every effect twice in development. Without
// guards we would inject two <script> tags and call init() twice. The
// bundle's init() is idempotent (the second call is a no-op), but we still
// avoid the duplicate <script> by tracking load state at module scope.

let scriptPromise: Promise<void> | null = null;

const loadBundle = (src: string): Promise<void> => {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Bug-Fab requires a browser environment."));
  }
  if (window.BugFab) {
    return Promise.resolve();
  }
  if (scriptPromise) {
    return scriptPromise;
  }
  // Reuse an existing tag if one is already on the page (e.g., HMR reload).
  const existing = document.querySelector<HTMLScriptElement>(
    `script[data-bug-fab-bundle][src="${src}"]`,
  );
  if (existing) {
    scriptPromise = new Promise<void>((resolve, reject) => {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error(`Failed to load Bug-Fab bundle: ${src}`)),
        { once: true },
      );
    });
    return scriptPromise;
  }
  scriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.defer = true;
    script.dataset.bugFabBundle = "true";
    script.addEventListener("load", () => resolve(), { once: true });
    script.addEventListener(
      "error",
      () => {
        scriptPromise = null;
        reject(new Error(`Failed to load Bug-Fab bundle: ${src}`));
      },
      { once: true },
    );
    document.head.appendChild(script);
  });
  return scriptPromise;
};

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface BugFabProviderProps {
  config: BugFabConfig;
  children: ReactNode;
}

/**
 * Wrap your app with this provider to enable Bug-Fab. It:
 *   1. Disables the bundle's auto-init (we control init explicitly).
 *   2. Injects the bundle <script> from `config.bundlePath`
 *      (default `/bug-fab/bug-fab.js`).
 *   3. Calls `window.BugFab.init(config)` once the bundle resolves.
 *   4. Calls `window.BugFab.destroy()` on unmount, restoring `window.fetch`
 *      and removing the FAB from the DOM.
 *
 * StrictMode-safe: handles React 18's double-invoke without double-injecting
 * scripts or double-mounting the FAB.
 */
export function BugFabProvider({
  config,
  children,
}: BugFabProviderProps): JSX.Element {
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState("");

  // Pin the latest config in a ref so callbacks always see fresh values
  // without re-running init() on every render.
  const configRef = useRef<BugFabConfig>(config);
  configRef.current = config;

  useEffect(() => {
    let cancelled = false;
    const bundlePath = config.bundlePath ?? "/bug-fab/bug-fab.js";

    // Tell the bundle not to auto-init on DOMContentLoaded — we drive init.
    if (typeof window !== "undefined") {
      window.BugFabAutoInit = false;
    }

    loadBundle(bundlePath)
      .then(() => {
        if (cancelled || !window.BugFab) return;
        const { bundlePath: _ignored, ...initConfig } = configRef.current;
        window.BugFab.init(initConfig);
        setVersion(window.BugFab.version);
        setReady(true);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        // Surface load failures via the user's onSubmitError callback if
        // provided — that's the same channel the bundle uses for runtime
        // errors, so consumers only need one error path.
        const onError = configRef.current.onSubmitError;
        if (typeof onError === "function") {
          try {
            onError(err);
          } catch {
            /* swallow */
          }
        } else {
          // eslint-disable-next-line no-console
          console.error(err);
        }
      });

    return () => {
      cancelled = true;
      // StrictMode will run this teardown immediately after the first mount,
      // then re-run the effect. destroy() is safe to call before init() has
      // resolved — it's a no-op if BugFab isn't on window yet.
      if (typeof window !== "undefined" && window.BugFab) {
        try {
          window.BugFab.destroy();
        } catch {
          /* swallow — best effort cleanup */
        }
      }
    };
    // We intentionally re-init only when the bundle path changes; other
    // config keys are read live from configRef on each call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.bundlePath]);

  const value: BugFabContextValue = {
    open: () => {
      if (typeof window !== "undefined" && window.BugFab) {
        window.BugFab.open();
      }
    },
    version,
    ready,
  };

  return (
    <BugFabContext.Provider value={value}>{children}</BugFabContext.Provider>
  );
}
