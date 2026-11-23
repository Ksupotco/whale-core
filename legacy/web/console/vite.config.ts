import path from "path";
import { fileURLToPath } from "url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    // 开发期同源代理：/api 转发到真实/测试 whale-api（不开放任何 CORS）
    proxy: {
      "/api": {
        target: process.env.WHALE_DEV_API || "http://127.0.0.1:8180",
        changeOrigin: false,
      },
    },
  },
});
