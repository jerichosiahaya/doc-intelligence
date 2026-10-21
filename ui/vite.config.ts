import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the browser calls /api and Vite forwards it to the server, so no CORS is needed.
const API = process.env.API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: API, rewrite: (path) => path.replace(/^\/api/, "") } },
  },
  test: { environment: "jsdom", globals: true, setupFiles: "./src/test-setup.ts" },
});
