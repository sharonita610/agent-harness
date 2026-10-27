#!/usr/bin/env bash
# 임베딩 서버 (llama.cpp, embeddinggemma-300m, ~300MB). OpenAI 호환 /v1/embeddings. 기본 포트 8082.
set -euo pipefail
MODEL="${EMBED_MODEL:-ggml-org/embeddinggemma-300m-qat-q8_0-GGUF}"
PORT="${EMBED_PORT:-8082}"
exec llama-server -hf "$MODEL" --embeddings -c 2048 -b 2048 -ub 2048 -ngl 99 --host 127.0.0.1 --port "$PORT" --alias local-embed   # -b/-ub: 청크 하나가 512 토큰을 넘어도 임베딩되게
