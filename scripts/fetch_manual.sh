#!/usr/bin/env bash
# ngspice 공식 매뉴얼 PDF(약 2.7MB, 765쪽)를 내려받아 지식 베이스를 만든다.
#   scripts/fetch_manual.sh            # 내려받기 + harness kb build
#   uv run harness kb embed --model local   # 임베딩(로컬 서버 scripts/serve_embed.sh) 또는 --model openai
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/kb/manual
if [ ! -f data/kb/manual/ngspice-manual.pdf ]; then
  curl -sSL -o data/kb/manual/ngspice-manual.pdf "https://ngspice.sourceforge.io/docs/ngspice-manual.pdf"
fi
ls -la data/kb/manual/ngspice-manual.pdf
uv run harness kb build --pdf data/kb/manual/ngspice-manual.pdf
