#!/usr/bin/env bash
# 로컬 LLM 서버 (llama.cpp). 첫 실행 시 모델을 HF 에서 내려받아 ~/.cache/huggingface/hub 에 둔다.
#
# 사용:
#   scripts/serve_llm.sh            # Hermes-4-14B Q4_K_M, 컨텍스트 65536 (YaRN 2x 자동)
#   scripts/serve_llm.sh 32768      # 컨텍스트 지정
#   LLM_MODEL=ggml-org/gpt-oss-20b-GGUF:MXFP4 scripts/serve_llm.sh        # 다른 HF 모델
#   LLM_MODEL_PATH=~/.cache/local-agent-lab/models/gpt-oss-20b-MXFP4.gguf scripts/serve_llm.sh   # 로컬 파일
#
# Hermes Agent 는 컨텍스트 64K 미만이면 실행을 거부한다. Hermes-4-14B(Qwen3 기반) 의 학습 컨텍스트는
# 40,960 이라 그 이상은 YaRN 스케일링이 필요하다 (짧은 대화 품질이 약간 떨어질 수 있음).
set -euo pipefail
MODEL="${LLM_MODEL:-bartowski/NousResearch_Hermes-4-14B-GGUF:Q4_K_M}"
MODEL_PATH="${LLM_MODEL_PATH:-}"
CTX="${1:-65536}"
PORT="${LLM_PORT:-8080}"

if [ -z "$MODEL_PATH" ] && [[ "${HARNESS_OFFLINE:-}" =~ ^(1|true|yes|on)$ ]]; then
  echo "[serve_llm] 오프라인 모드(HARNESS_OFFLINE=1)에서는 Hugging Face 에서 받지 않습니다. LLM_MODEL_PATH=/path/model.gguf 를 지정하세요" >&2
  exit 1
fi

if [ -n "$MODEL_PATH" ]; then
  MODEL_ARGS=(-m "$MODEL_PATH")
else
  MODEL_ARGS=(-hf "$MODEL")
fi

ROPE_ARGS=()
if [ -z "$MODEL_PATH" ] && [[ "$MODEL" == *"Hermes-4-14B"* ]] && [ "$CTX" -gt 40960 ]; then
  ROPE_ARGS=(--rope-scaling yarn --rope-scale 2 --yarn-orig-ctx 40960)
  echo "[serve_llm] Hermes-4-14B 학습 컨텍스트(40960) 초과 → YaRN 2x 적용" >&2
fi

exec llama-server "${MODEL_ARGS[@]}" \
  --jinja \
  -c "$CTX" \
  ${ROPE_ARGS[@]+"${ROPE_ARGS[@]}"} \
  -fa on -ctk q8_0 -ctv q8_0 \
  -ngl 99 \
  --host 127.0.0.1 --port "$PORT" \
  --alias local-llm
