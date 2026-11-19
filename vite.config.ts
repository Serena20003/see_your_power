import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local dev: proxy /v1 and health checks to the FastAPI service.
// Override the target with RUNWAY_DEV_API_URL if running the API elsewhere.
const API_TARGET = process.env.RUNWAY_DEV_API_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": { target: API_TARGET, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
      "/healthz": { target: API_TARGET, changeOrigin: true },
    },
  },
});
