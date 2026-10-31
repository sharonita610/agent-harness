#!/usr/bin/env bash
# 애플 실리콘용 두 번째 서빙 엔진: MLX(mlx_lm.server, OpenAI 호환). llama.cpp 와 같은 모델로 엔진 비교(ADR-029)에 쓴다.
#
#   scripts/serve_mlx.sh                                   # mlx-community/gpt-oss-20b-MXFP4-Q4, 포트 8081
#   MLX_MODEL=mlx-community/gpt-oss-20b-MXFP4-Q8 scripts/serve_mlx.sh
#   MLX_MODEL_PATH=/path/to/model-dir scripts/serve_mlx.sh # 로컬 폴더(에어갭)
#   MLX_PORT=8091 scripts/serve_mlx.sh                     # 8081 이 다른 서버(Expo 등)에 잡혀 있을 때
#
# 하네스에서 쓰기: --provider "compat:http://127.0.0.1:8081/v1|mlx-community/gpt-oss-20b-MXFP4-Q4"
# mlx_lm.server 는 도구 호출을 파싱하지 않고 harmony 원문을 content 로 돌려준다 → 하네스가 건진다(providers.parse_harmony).
# gpt-oss 가 분석 채널에서 같은 문장을 반복하다 길이 제한에 걸리는 일이 잦다 → 반복 벌점을 같이 준다:
#   HARNESS_LLM_EXTRA_BODY='{"repetition_penalty": 1.1, "repetition_context_size": 64}'
# 두 엔진을 동시에 띄우지 않는다(24GB 맥에서는 메모리가 모자란다).
set -euo pipefail
MODEL="${MLX_MODEL:-mlx-community/gpt-oss-20b-MXFP4-Q4}"
MODEL_PATH="${MLX_MODEL_PATH:-}"
PORT="${MLX_PORT:-8081}"
if [ -z "$MODEL_PATH" ] && [[ "${HARNESS_OFFLINE:-}" =~ ^(1|true|yes|on)$ ]]; then
  echo "[serve_mlx] 오프라인 모드에서는 Hugging Face 에서 받지 않습니다. MLX_MODEL_PATH=/path/model-dir 를 지정하세요" >&2
  exit 1
fi
exec uv run --with mlx-lm python -m mlx_lm.server --model "${MODEL_PATH:-$MODEL}" --host 127.0.0.1 --port "$PORT" --max-tokens 2048 --log-level WARNING
