# ADR-010 인증·인가·감사: 결재는 결재자만, 누가 했는지 남긴다

날짜: 2026-10-28 · 상태: 채택

## 문제
승인 흐름이 핵심인데 `POST /approvals/{id}` 를 아무나 부를 수 있었다. "사람이 승인했다" 는 말이 성립하려면 그 사람이 누구인지 증명하고 기록해야 한다. 서브원·SK 공고의 "인증·인가·Tool 권한·Human-in-the-loop" 항목.

## 결정
1. **API 키 + 역할.** `viewer < operator < approver < admin`. 질문·잡 제출은 operator, 결재는 approver, 사용자·감사 조회는 approver/admin. 키는 `harness users add 이름 --role approver` 로 발급하고 파일(`data/users.json`)엔 SHA-256 해시만 둔다. 발급 때 한 번만 보인다.
2. **개발 모드는 명시적으로.** 사용자가 하나도 없으면 익명 `dev`(admin) 로 열어 데모가 막히지 않게 하되, `/me` 와 화면 상단에 "개발 모드, 인증 없음" 을 표시한다. `HARNESS_AUTH=1` 로 강제할 수 있다.
3. **감사 기록.** `audit` 테이블에 누가(user, role) 언제 무엇을(action: chat, job.submit, job.cancel, approval.approve/deny) 어디에(target) 했는지와 IP 를 남긴다. 결재 레코드의 `decided_by` 는 사용자 이름이다. `GET /audit` 는 approver 이상.
4. **화면.** 키 한 줄 로그인(localStorage), 상단에 이름·역할, 결재자가 아니면 전표에 버튼 대신 "결재자가 처리할 때까지 기다립니다", 처리된 전표에 결재자 이름.

## 대안과 기각 이유
- 세션 로그인(아이디·비밀번호·쿠키): 포트폴리오 범위에선 키가 단순하고 테스트하기 쉽다. SSO/OIDC 는 운영 단계에서 붙인다.
- 역할 없이 키만: "운영자가 자기 변경을 자기가 승인" 하는 구멍이 남는다. 역할 분리가 승인 흐름의 핵심이다.

## 결과
테스트 5개(401/403/결재자 승인/감사 기록/해시 저장/개발 모드). 전체 54개.
