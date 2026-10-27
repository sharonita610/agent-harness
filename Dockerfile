# ---- 1) React 화면 빌드
FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# ---- 2) 파이썬 서비스
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY harness/ harness/
COPY scripts/ scripts/
COPY --from=ui /ui/dist ui/dist
RUN uv sync --frozen --no-dev
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fsS http://127.0.0.1:8787/health || exit 1
ENTRYPOINT ["/entrypoint.sh"]
