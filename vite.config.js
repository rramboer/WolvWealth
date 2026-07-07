import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds the React dashboard (wolvwealth/js) into wolvwealth/static/js/bundle.js,
// which the Flask optimizer template loads with <script type="module">.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  build: {
    outDir: "wolvwealth/static/js",
    emptyOutDir: false, // static/js also holds the hand-written site.js
    sourcemap: mode !== "production",
    rollupOptions: {
      input: "wolvwealth/js/main.jsx",
      output: {
        entryFileNames: "bundle.js",
        chunkFileNames: "[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
}));
