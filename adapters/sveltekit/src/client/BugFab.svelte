<script lang="ts">
  // BugFab.svelte — type-safe Svelte wrapper around the upstream Bug-Fab
  // browser bundle (`window.BugFab.init({...})`).
  //
  // The upstream bundle is published as a static asset (`bug-fab.js`) and
  // mounted by the consumer's app — see README § "Mounting the static
  // bundle" for build-time copy instructions. This component does NOT bundle
  // the upstream JS (it would balloon the npm package and pin html2canvas
  // versions). It just imports it via a `<script>` tag at runtime, then
  // calls `window.BugFab.init(...)` once.
  //
  // SSR-safe: all DOM access is guarded behind `onMount` so server rendering
  // does not hit `window` / `document`.
  import { onMount, onDestroy } from 'svelte';

  /** Where the consumer's intake endpoint is mounted. e.g. "/api/bug-reports". */
  export let intakeEndpoint: string;
  /**
   * URL to the upstream Bug-Fab browser bundle. Default points at a same-origin
   * static asset; override if you serve it from a CDN.
   */
  export let bundleSrc = '/bug-fab/bug-fab.js';
  /** Optional logical area / route name for context tagging. */
  export let module: string | undefined = undefined;
  /** Optional consumer app version to attach to context. */
  export let appVersion: string | undefined = undefined;
  /** Optional environment string (dev / staging / prod). */
  export let environment: string | undefined = undefined;
  /** Optional pre-resolved reporter identity from your auth layer. */
  export let reporter: { name?: string; email?: string; user_id?: string } | undefined = undefined;
  /** Suppress the FAB entirely (for pages where it shouldn't appear). */
  export let disabled = false;

  let initialized = false;
  let scriptEl: HTMLScriptElement | null = null;

  onMount(() => {
    if (disabled) return;
    if (typeof window === 'undefined') return;

    type BugFabGlobal = {
      init: (opts: Record<string, unknown>) => void;
      destroy?: () => void;
    };
    const w = window as unknown as { BugFab?: BugFabGlobal };

    const initWhenReady = (): void => {
      if (!w.BugFab) return;
      w.BugFab.init({
        intakeEndpoint,
        module,
        appVersion,
        environment,
        reporter
      });
      initialized = true;
    };

    if (w.BugFab) {
      initWhenReady();
      return;
    }

    // Inject the bundle script once.
    const existing = document.querySelector<HTMLScriptElement>(`script[data-bug-fab="bundle"]`);
    if (existing) {
      existing.addEventListener('load', initWhenReady, { once: true });
      return;
    }

    const s = document.createElement('script');
    s.src = bundleSrc;
    s.async = true;
    s.dataset.bugFab = 'bundle';
    s.addEventListener('load', initWhenReady, { once: true });
    document.head.appendChild(s);
    scriptEl = s;
  });

  onDestroy(() => {
    if (typeof window === 'undefined') return;
    if (!initialized) return;
    type BugFabGlobal = { destroy?: () => void };
    const w = window as unknown as { BugFab?: BugFabGlobal };
    if (w.BugFab?.destroy) {
      try {
        w.BugFab.destroy();
      } catch {
        // Best-effort.
      }
    }
    if (scriptEl?.parentNode) {
      // We intentionally do NOT remove the script tag — multi-instance pages
      // may still depend on the loaded bundle.
    }
  });
</script>

<!-- The upstream bundle owns its own DOM; this component renders nothing. -->
