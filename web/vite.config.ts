import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    proxy: {
      "/launcher/api": "http://localhost:18080",
      "/launcher/w": { target: "http://localhost:18080", ws: true },
    },
  },
});
