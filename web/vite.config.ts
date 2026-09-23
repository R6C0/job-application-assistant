import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by FastAPI from web/dist, so `base` stays "/".
// In dev, Vite serves the UI and proxies the API to the Python process, which
// means you get hot reload without a second origin or any CORS configuration.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // One vendor chunk. The app is small and local; a waterfall of tiny
    // chunks would be slower than a single file over loopback.
    chunkSizeWarningLimit: 900,
  },
});
