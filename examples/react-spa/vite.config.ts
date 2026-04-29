import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite dev server proxies two paths to the Bug-Fab backend:
 *
 *   /api/bug-reports      → POST intake (the protocol's submit endpoint).
 *   /bug-fab/bug-fab.js   → the vanilla bundle, served by the FastAPI
 *                           reference adapter at /bug-fab/static/bug-fab.js.
 *                           We rewrite to drop the implicit /static segment
 *                           so the React provider can use the cleaner path.
 *
 * Run the FastAPI example (examples/fastapi-minimal) on :8000 alongside
 * this dev server. Or copy `bug-fab.js` into `public/` to skip the bundle
 * proxy entirely.
 */
const BACKEND_URL = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/bug-reports": {
        target: BACKEND_URL,
        changeOrigin: true,
      },
      "/bug-fab/bug-fab.js": {
        target: BACKEND_URL,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/bug-fab\//, "/bug-fab/static/"),
      },
      "/bug-fab/vendor": {
        target: BACKEND_URL,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/bug-fab\//, "/bug-fab/static/"),
      },
    },
  },
});
