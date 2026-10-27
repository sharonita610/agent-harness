# ADR-007 관측성: 이벤트 → 스팬, JSONL + OpenTelemetry

날짜: 2026-10-28 · 상태: 채택

## 문제
"에이전트가 왜 그렇게 답했는가" 를 사후에 재구성할 수 있어야 한다. 공고(SK, 서브원, 알프레드, 티맵)는 Logging·Tracing·Observability 를 명시한다. 루프 코드에 로깅을 흩뿌리지 않고 붙일 방법이 필요했다.

## 결정
1. **스팬의 원천은 이미 있는 이벤트.** 루프는 `on_event` 로 step/llm/tool_call/tool_result/approval/final 을 내보낸다. `TraceCollector` 가 이 이벤트만 보고 스팬 트리(run → llm.stepN / tool.name → approval.name)를 만든다. 루프 코드 변경 0줄.
2. **세 곳이 같은 수집기를 쓴다.** CLI(`ask --trace`), 평가 러너(실행마다 `results/traces/…/<task>_runN.jsonl`), 서비스(저장된 이벤트의 ts 로 재구성, `GET /runs/{id}/trace`). 서비스는 이벤트를 먼저 SQLite 에 남기므로 트레이스는 파일이 아니라 이벤트 테이블이 원본이다.
3. **내보내기는 표준으로.** JSONL(의존성 0) 을 기본으로 두고, `HARNESS_OTLP_ENDPOINT` 가 있으면 OpenTelemetry OTLP/HTTP 로 전송한다. 시작·종료 시각을 명시해 사후 생성 스팬을 올리므로 Jaeger·Langfuse·Phoenix 어디든 받는다. 검증은 Jaeger all-in-one(docker)로 했다.
4. **스팬 속성은 작게.** 인자·에러는 300~2,000자에서 자르고, 답변 본문은 길이만 넣는다. 본문은 이벤트 테이블·JSONL 에 있다.

## 대안과 기각 이유
- 처음부터 OTel API 로 루프를 계측: 평가·CLI 처럼 OTel 이 필요 없는 경로까지 의존성이 생기고, 테스트가 무거워진다.
- Langfuse SDK 직접 통합: 벤더 종속. OTLP 로 보내면 Langfuse 도 받는다.

## 결과
테스트 3개(라이브 실행·저장 이벤트 재구성·취소 시 미완 스팬). 가짜 프로바이더 실행 7스팬이 Jaeger 에 waterfall 로 표시됨. 실제 모델 실행도 OTLP 로 전송 확인.
