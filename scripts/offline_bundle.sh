#!/usr/bin/env bash
# 에어갭 설치 묶음 만들기. 인터넷이 되는 곳에서 한 번 만들고, USB 등으로 들고 들어가 인터넷 없이 설치한다.
#
#   scripts/offline_bundle.sh docker [--model /path/model.gguf]   # 도커 이미지 tar + 모델 + compose + 설치 안내 (권장)
#   scripts/offline_bundle.sh source [--model /path/model.gguf]   # 소스 + 파이썬 휠 캐시(uv) + 빌드된 화면 + 모델 (도커 없는 서버용)
#
# 결과: dist-offline/agent-harness-offline-<날짜>/ 와 같은 이름의 .tar.gz. 안의 INSTALL.md 를 따른다.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-docker}"; shift || true
MODEL=""
while [ $# -gt 0 ]; do case "$1" in --model) MODEL="$2"; shift 2;; *) echo "모르는 옵션: $1" >&2; exit 2;; esac; done

STAMP="$(date +%Y%m%d)"
OUT="dist-offline/agent-harness-offline-${STAMP}"
rm -rf "$OUT"; mkdir -p "$OUT"
VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"

if [ "$MODE" = "docker" ]; then
  echo "[1/3] 도커 이미지 빌드 (agent-harness:${VERSION})"
  docker build -t "agent-harness:${VERSION}" .
  echo "[2/3] 이미지 저장"
  docker save "agent-harness:${VERSION}" | gzip > "$OUT/agent-harness-image-${VERSION}.tar.gz"
  cp docker-compose.yml "$OUT/"
  sed -i.bak "s#image: .*#image: agent-harness:${VERSION}#" "$OUT/docker-compose.yml" 2>/dev/null || true; rm -f "$OUT/docker-compose.yml.bak"
elif [ "$MODE" = "source" ]; then
  echo "[1/3] 소스 묶기 (git archive: 커밋된 파일 기준이므로 먼저 커밋한다)"
  git archive --format=tar HEAD | tar -x -C "$OUT" 2>/dev/null || cp -R harness scripts pyproject.toml uv.lock README.md "$OUT/"
  echo "[2/3] 파이썬 휠을 uv 캐시로 받아 두기 (대상 서버에서 uv sync --offline 이 이 캐시를 쓴다)"
  # 기존 .venv 가 이미 맞으면 uv 가 아무것도 받지 않으므로, 임시 환경에 새로 설치해 캐시를 채운다
  UV_CACHE_DIR="$OUT/uv-cache" UV_PROJECT_ENVIRONMENT="$OUT/.venv-tmp" uv sync --frozen --no-dev --no-install-project >/dev/null
  rm -rf "$OUT/.venv-tmp"
  echo "      화면 빌드 (node 가 없는 서버를 위해 결과물만 담는다)"
  (cd ui && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
  mkdir -p "$OUT/ui" && cp -R ui/dist "$OUT/ui/dist"
else
  echo "모드는 docker 또는 source" >&2; exit 2
fi

echo "[3/3] 모델·안내"
if [ -n "$MODEL" ]; then mkdir -p "$OUT/models" && cp "$MODEL" "$OUT/models/"; fi
cat > "$OUT/INSTALL.md" <<MD
# agent-harness 에어갭 설치 (${STAMP}, 소스 ${VERSION})

이 묶음은 인터넷 없이 설치한다. 모델 파일(models/*.gguf)이 없으면 같은 폴더에 넣는다.

## 도커로
1. \`docker load < agent-harness-image-${VERSION}.tar.gz\`
2. \`.env\` 에 \`HARNESS_OFFLINE=1\`, \`LLM_BASE_URL=http://<사내 모델 서버>:8080/v1\` (모델 서버는 llama-server 를 같은 묶음의 models/ 로 띄운다)
3. \`docker compose up -d\` → http://<서버>:8787/app

## 소스로 (도커 없는 서버)
1. \`uv\` 와 Python 3.12, ngspice(회로 팩을 쓸 때)를 설치 매체로 넣는다.
2. \`UV_CACHE_DIR=\$PWD/uv-cache uv sync --frozen --no-dev --offline\`
3. 모델 서버: \`LLM_MODEL_PATH=\$PWD/models/<파일>.gguf scripts/serve_llm.sh 32768\`
4. 서비스: \`HARNESS_OFFLINE=1 uv run uvicorn harness.service.app:app --port 8787\` (화면은 ui/dist 를 그대로 서빙)

## 확인
- \`curl http://127.0.0.1:8787/health\` 의 \`offline\` 이 true
- \`HARNESS_OFFLINE=1 uv run pytest tests/test_offline.py\` 가 통과(바깥으로 나가는 소켓 연결이 없음을 확인)
- 외부 공급자(\`openai:\`, \`anthropic:\`)와 바깥 주소의 임베딩·트레이스 설정은 시작 시 거부된다
MD
(cd dist-offline && tar -czf "agent-harness-offline-${STAMP}.tar.gz" "agent-harness-offline-${STAMP}")
echo "완료: dist-offline/agent-harness-offline-${STAMP}.tar.gz"
du -sh "dist-offline/agent-harness-offline-${STAMP}.tar.gz"
