import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发期把 /api 请求代理到本地 FastAPI（8000 端口），前端代码里只写相对路径
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
