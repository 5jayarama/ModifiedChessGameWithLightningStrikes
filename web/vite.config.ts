import { defineConfig } from "vite";

// In development the Flask API runs on :5000 (flask --app server.app run) and Vite proxies to it.
// In production Flask serves the built files from web/dist, so everything is same-origin.
export default defineConfig({
  server: { proxy: { "/api": "http://127.0.0.1:5000" } },
  build: { outDir: "dist", emptyOutDir: true },
});
