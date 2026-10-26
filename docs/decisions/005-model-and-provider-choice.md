# ADR-005 모델·프로바이더 선택

날짜: 2026-10-27 · 상태: 채택

## 문제
공고들은 상용 LLM 과 오픈소스 LLM 을 둘 다 다루고 비용·지연을 비교할 수 있는 사람을 원한다. 개발 중엔 비용 0 으로 많이 돌려봐야 한다.

## 결정
- **프로바이더 추상화 1개**(`Provider.complete(messages, tools) -> LLMResponse`). OpenAI 호환(llama.cpp, vLLM, LM Studio, OpenAI)과 Anthropic 두 구현. 설정 문자열 한 줄(`local`, `anthropic:claude-sonnet-4-5`, `openai:gpt-5-mini`, `compat:URL|model`)로 교체.
- **기본은 로컬** Hermes-4-14B Q4_K_M. 이유: 24GB 맥에서 32K 컨텍스트와 함께 돌아가는 가장 큰 축의 모델, Nous 가 함수 호출 포맷을 학습시킨 계열, Apache 2.0.
- **llama.cpp 서버** 직접 사용(Ollama 아님): `--jinja` 로 네이티브 함수 호출, 컨텍스트 길이를 명시 제어, 프롬프트 캐시가 시스템 프롬프트+도구 스키마(≈3.9K 토큰)를 재사용한다.
- 메시지 포맷은 OpenAI 형식을 내부 표준으로 두고 Anthropic 은 어댑터에서 변환한다.

## 측정(1일차)
생성 21~25 tok/s, 프롬프트 처리 100~135 tok/s(q8 KV 캐시). 프롬프트 캐시 덕에 2번째 호출부터는 신규 토큰만 처리.

## 다음
f16 KV 캐시와 16K 컨텍스트로 프롬프트 처리 속도 비교. 상용 모델 2종으로 같은 평가를 돌려 정확도·지연·비용 3축 표 작성.
