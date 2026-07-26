/**
 * Background: During AsterMem frontend development, the FastAPI backend runs locally (default port 8641);
 * in production, FastAPI directly serves dist/, and the frontend uniformly requests relative paths /api/....
 * Design intent: Only proxy /api to the backend in the dev server; VITE_API_TARGET can override the target,
 * so build artifacts contain no hardcoded hostnames—zero-config deployment.
 * Key constraint: Do not inject other define constants here; keep the build reproducible.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8641",
        changeOrigin: true,
      },
    },
  },
});
