import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발: `npm run dev` (5173) → API 는 VITE_API(기본 8787)로 프록시. 빌드: dist/ 를 FastAPI 가 /app 에서 서빙.
const API = process.env.VITE_API ?? "http://127.0.0.1:8787";
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    proxy: Object.fromEntries(
      [
        "/chat",
        "/sessions",
        "/approvals",
        "/jobs",
        "/runs",
        "/health",
        "/tools",
        "/me",
        "/audit",
        "/workspace",
        "/dashboard",
        "/users",
        "/mcp",
        "/a2a",
        "/.well-known",
        "/files",
        "/docs",
        "/openapi.json",
      ].map((p) => [p, { target: API, changeOrigin: true }]),
    ),
  },
  test: { environment: "node" },
});
