# 1~3일차 검증 리포트 (2026-10-27)

사용자 검증 없이 진행한 범위와, 직접 확인하는 방법.

## 만든 것
| 항목 | 위치 | 상태 |
|---|---|---|
| 로컬 LLM 서버 스크립트 | `scripts/serve_llm.sh` | 모델 9GB 다운로드 완료(`~/.cache/huggingface/hub/`), 함수 호출 동작 확인 |
| 샘플 회사 DB 생성기 | `harness/packs/enterprise/seed.py` | 재실행해도 동일(sha256 앞 16자 `92d0c19eb4f1cd2a`) |
| 규정 문서 10개 | `harness/packs/enterprise/docs/` | 06, 09 번에 함정 지시문 포함 |
| 에이전트 루프 | `harness/agent/loop.py` | 프레임워크 없이 ~230줄 |
| 도구 10개 + 권한 | `harness/packs/enterprise/tools.py`, `harness/tools/` | read/write/destructive |
| MCP 서버 | `harness/mcp_server/enterprise_server.py` | `scripts/mcp_check.py` 로 검증 통과 |
| 평가 | `harness/evals/` | 16태스크, 1회차 81% |
| 테스트 | `tests/` | 31개 통과 |
| 문서 | `README.md`, `NOTES.md`, `docs/decisions/*.md`(5개) | |

## 직접 확인하는 순서 (약 15분)
```bash
cd ~/IdeaProjects/local-agent-lab
uv run pytest -q                              # 1) 31 passed
uv run harness seed                           # 2) sha256_16: 92d0c19eb4f1cd2a
scripts/serve_llm.sh                          # 3) 터미널 하나 더 열어 실행. 로딩 ~15초. CLI 는 /health 가 ok 될 때까지 자동 대기한다
uv run harness ask "현재 상태가 '지연'인 주문은 총 몇 건이야?"      # 4) 255건, run_sql 1회
uv run harness chat                           # 5) "주문 353번 상태를 출고로 바꿔줘" → 승인 프롬프트(y/N) 가 뜨는지
uv run python scripts/mcp_check.py            # 6) MCP CHECK OK
uv run harness eval --n 1                     # 7) 10~15분. harness/evals/results/ 에 표 생성
```
서버 종료: 실행한 터미널에서 Ctrl-C.

## 확인된 한계 (다음 작업 재료)
- 14B 로컬 모델은 표 독해와 도구 생략에서 틀린다(d02, m02). 상용 모델 비교표가 있어야 "로컬 vs 상용" 주장이 선다.
- 같은 질문도 실행마다 SQL 이 달라진다(q04: 149/120/149). 평가는 n≥3 으로.
- 문서 검색은 BM25 접두 검색만. 임베딩 하이브리드는 2주차.
- 승인은 CLI y/n 만. 서비스(SSE+승인 API)는 2주차.

## 하지 않은 것
- GitHub 푸시(로컬 커밋만), Hermes Agent 설치, 유료 API 호출.
