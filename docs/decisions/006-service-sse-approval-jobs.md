# ADR-006 서비스 계층: SSE 이벤트 스트림, 승인 브로커, 비동기 잡 큐

날짜: 2026-10-28 · 상태: 채택

## 문제
터미널 스크립트는 제품이 아니다. 공고들이 요구하는 것은 "에이전트 실행 흐름과 대화 스트리밍 서버"(캐치테이블), "비동기 작업 큐"(알세미), "Human-in-the-loop"(서브원·SK) 이다. 동기 루프인 에이전트를 웹 서비스로 감싸되 루프 코드는 바꾸지 않아야 한다.

## 결정
1. **에이전트는 그대로, 실행은 스레드에서.** `Agent.run()` 은 동기다. FastAPI 요청 핸들러는 `run_in_executor` 로 스레드에 넘기고, 루프의 `on_event` 콜백이 `loop.call_soon_threadsafe` 로 asyncio 큐에 이벤트를 넣는다. 스레드 종료는 큐에 종료 표식을 넣어 알린다(초기 구현은 15초 ping 타임아웃까지 기다리는 버그가 있었고 테스트 46초 → 1초로 줄었다).
2. **스트리밍 단위는 토큰이 아니라 이벤트.** `POST /chat` 은 `text/event-stream` 으로 `run → step → tool_call → tool_result → approval_pending → approval_result → final → done` 을 보낸다. 토큰 단위 스트리밍은 프로바이더에 스트림 API 를 붙이면 되지만, 에이전트 UX 에서 사용자가 기다리는 정보는 "지금 어떤 도구를 왜 부르는가" 라서 이벤트 스트림을 먼저 했다.
3. **승인은 브로커가 중재한다.** 변경 도구를 만나면 에이전트 스레드가 `ApprovalBroker.wait()` 에서 막히고, SSE 로 `approval_pending {approval_id}` 가 나간다. 사람이 `POST /approvals/{id}` 로 결정하면 `threading.Event` 가 풀려 루프가 이어진다. 제한 시간(기본 10분) 이 지나면 `expired` 로 거부 처리된다. 루프의 승인 핸들러 인터페이스(`(tool, args) -> bool`) 는 CLI 와 동일하다.
4. **잡 큐는 같은 실행기를 재사용한다.** `POST /jobs` 는 run 을 `queued` 로 저장하고 in-process 워커(동시성 1, 로컬 모델 기준)가 순서대로 `execute_run` 을 돌린다. 취소는 협조적이다: 플래그를 세우면 에이전트가 다음 스텝 시작 전에 멈춘다(`stopped_reason=cancelled`). Redis/arq 는 프로세스가 여러 개가 될 때 바꾼다.
5. **모든 것을 SQLite 에 남긴다.** 세션·메시지·실행·이벤트(seq 순)·승인. `GET /runs/{id}` 가 그 실행의 전체 트레이스다. 외부 트레이싱(Langfuse 등)은 이 이벤트를 내보내는 어댑터로 붙인다.

## 대안과 기각 이유
- WebSocket: 양방향이 필요한 건 승인뿐이고, 승인은 별도 POST 로 충분하다. SSE 가 프록시·브라우저 호환이 단순하다.
- 에이전트를 async 로 재작성: 도구(SQLite, 파일)가 동기라 이득이 없고 CLI·MCP·평가가 같은 코드를 쓴다는 장점을 잃는다.
- 승인 없이 변경 도구 자동 실행: 정책(ADR-002) 위반.

## 결과
테스트 5개(`tests/test_service.py`, 가짜 프로바이더로 1초). 실제 모델(gpt-oss-20b)로 데모 페이지에서 "메모 남겨줘" → 승인 카드 → 승인 → 메모 ID 121 기록 → 답변까지 20초(사람 대기 포함). 잡 큐로 "등급별 집계 표" 질문이 running → done.
