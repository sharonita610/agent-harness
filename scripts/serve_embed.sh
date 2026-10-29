#!/usr/bin/env bash
# 임베딩 서버 (llama.cpp, embeddinggemma-300m, ~300MB). OpenAI 호환 /v1/embeddings. 기본 포트 8082.
set -euo pipefail
MODEL="${EMBED_MODEL:-ggml-org/embeddinggemma-300m-qat-q8_0-GGUF}"
if [[ "${HARNESS_OFFLINE:-}" =~ ^(1|true|yes|on)$ ]] && [ -z "${EMBED_MODEL_PATH:-}" ]; then
  echo "[serve_embed] 오프라인 모드에서는 Hugging Face 에서 받지 않습니다. EMBED_MODEL_PATH=/path/model.gguf 를 지정하세요" >&2; exit 1
fi
PORT="${EMBED_PORT:-8082}"
if [ -n "${EMBED_MODEL_PATH:-}" ]; then MODEL_ARGS=(-m "$EMBED_MODEL_PATH"); else MODEL_ARGS=(-hf "$MODEL"); fi
exec llama-server "${MODEL_ARGS[@]}" --embeddings -c 2048 -b 2048 -ub 2048 -ngl 99 --host 127.0.0.1 --port "$PORT" --alias local-embed   # -b/-ub: 청크 하나가 512 토큰을 넘어도 임베딩되게
