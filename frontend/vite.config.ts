import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";
import svgr from "vite-plugin-svgr";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), svgr()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    // Slow-network (3G) performance: keep the entry chunk small and split
    // stable vendor code into separate, long-cacheable files. Heavy,
    // page-specific dependencies (recharts, react-pdf) are pulled in by
    // the lazy route chunks that need them, never by the entry chunk.
    rollupOptions: {
      output: {
        // Function form: the object form fails the OutputOptions overload
        // under this Rollup/Vite type combination.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("recharts")) return "vendor-charts";
          if (id.includes("lucide-react")) return "vendor-icons";
          if (
            id.includes("react-hook-form") ||
            id.includes("@hookform") ||
            id.includes("zod")
          ) {
            return "vendor-forms";
          }
          if (
            /node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(
              id
            )
          ) {
            return "vendor-react";
          }
          return undefined;
        },
      },
    },
    // The vendor chunks above are intentionally larger than the default
    // 500 kB warning threshold; the entry chunk is what must stay small.
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
