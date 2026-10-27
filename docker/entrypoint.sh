#!/bin/sh
# DB 가 없으면 시드. 그 다음 API 서버.
set -e
cd /app
if [ "${HARNESS_PACK:-enterprise}" = "enterprise" ] && [ ! -f data/enterprise.db ]; then
  echo "[entrypoint] data/enterprise.db 없음 → 시드 생성"
  uv run --no-sync harness seed
fi
if [ "${HARNESS_PACK:-enterprise}" = "factory" ] && [ ! -f data/factory.db ]; then
  echo "[entrypoint] data/factory.db 없음 → 시드 생성"
  uv run --no-sync harness seed --pack factory
fi
exec uv run --no-sync uvicorn harness.service.app:app --host 0.0.0.0 --port 8787 --proxy-headers
