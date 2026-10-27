import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발: `npm run dev` (5173) → API 는 8787 로 프록시. 빌드: dist/ 를 FastAPI 가 /app 에서 서빙.
const API = "http://127.0.0.1:8787";
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    proxy: Object.fromEntries(["/chat", "/sessions", "/approvals", "/jobs", "/runs", "/health", "/tools"].map((p) => [p, { target: API, changeOrigin: true }])),
  },
  test: { environment: "node" },
});
