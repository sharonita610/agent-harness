# 실험 노트

## 2026-10-27 · 1~3일차

### 환경
- M4 Pro 24GB, macOS 26.5.1, llama.cpp 0.5.0 (brew `llama-server`), Python 3.12 (uv)
- 모델: bartowski/NousResearch_Hermes-4-14B-GGUF Q4_K_M (9.0GB). 다운로드 ~15분, 캐시 `~/.cache/huggingface/hub/`
- 서버: `scripts/serve_llm.sh 32768` = `--jinja -c 32768 -fa on -ctk q8_0 -ctv q8_0 -ngl 99`, 슬롯 4개(기본), KV 통합

### 측정
| 항목 | 값 |
|---|---|
| 생성 속도 | 21~25 tok/s |
| 프롬프트 처리 | 짧은 프롬프트 100~115 tok/s, 20K 토큰 프롬프트 135 tok/s (q8 KV 캐시 영향 추정, f16 비교 예정) |
| 프롬프트 캐시 | 같은 접두(시스템 프롬프트+도구 스키마 ≈3.9K 토큰) 재사용됨 → 2번째 호출부터 신규 토큰만 처리 |
| 함수 호출 | OpenAI 형식 `tools` 로 네이티브 동작 확인 (`finish_reason: tool_calls`, JSON 인자) |
| 시스템 메모리 | 모델 로드 후 여유 13% (다른 앱 켜진 상태) |

### 발견한 실패 모드와 대응 (평가 리포트 재료)
1. **도구를 글로 쓴다** — "지난달 재고 부족 지연 주문" 질문에 SQL 과 `search_docs(...)` 를 코드블록으로 적고 끝냄(도구 호출 0회).
   → 루프에 1회 보정(nudge): 답변에 ```/함수이름(/SELECT…FROM 패턴이 보이면 "실제 함수 호출로 실행하라" 고 되묻기. 시스템 프롬프트 원칙 0 추가.
2. **같은 호출을 25번 반복 생성** — `search_docs({"query":"할인 승인자"})` 를 25개 생성하다 max_tokens(1024) 에 걸림. 24개가 실행돼 다음 호출 프롬프트가 20K 토큰 → 149초.
   → 스텝 안 중복 호출 1회만 실행 + 스텝당 호출 수 상한 4개. 잘린 마지막 호출(`{`)은 유효 호출이 있으면 조용히 버림.
3. **승인 없는 변경 시도** — 아직 관찰 안 됨(평가 w01/w02 에서 확인 예정).

### 재현 명령
```bash
scripts/serve_llm.sh                 # 터미널 1
uv run harness seed                  # DB 생성 (sha256 92d0c19eb4f1cd2a 로 동일해야 함)
uv run harness ask "현재 상태가 '지연'인 주문은 총 몇 건이야?" --approve deny
uv run pytest -q                      # 30개
uv run python scripts/mcp_check.py    # MCP 서버 확인
uv run harness eval --n 1            # 평가 16개
```
