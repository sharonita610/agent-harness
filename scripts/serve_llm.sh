#!/usr/bin/env bash
# 로컬 LLM 서버 (llama.cpp). 첫 실행 시 모델(~9GB)을 HF 에서 내려받아 ~/Library/Caches/llama.cpp 에 둔다.
# 사용: scripts/serve_llm.sh [ctx]   예) scripts/serve_llm.sh 32768
set -euo pipefail
MODEL="${LLM_MODEL:-bartowski/NousResearch_Hermes-4-14B-GGUF:Q4_K_M}"
CTX="${1:-32768}"
PORT="${LLM_PORT:-8080}"
exec llama-server -hf "$MODEL" \
  --jinja \
  -c "$CTX" \
  -fa on -ctk q8_0 -ctv q8_0 \
  -ngl 99 \
  --host 127.0.0.1 --port "$PORT" \
  --alias local-llm
