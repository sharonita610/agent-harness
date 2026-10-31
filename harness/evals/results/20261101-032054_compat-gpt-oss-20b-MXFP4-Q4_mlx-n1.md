# 평가 결과 · compat:http://127.0.0.1:8091/v1|mlx-community/gpt-oss-20b-MXFP4-Q4 (mlx-community/gpt-oss-20b-MXFP4-Q4) · n=1

- 통과율 62% (16회 실행, 태스크 16개) · 유형별 query 40%, mixed 50%, doc 67%, write 100%, trap 75%
- 평균 스텝 2.12 · 지연 p50 11.0s / p95 66.3s · 실행당 토큰 5667 · 비용 $0.0000 (호스팅이면 $0.0032) · 프롬프트 v1

| id | 유형 | 통과율 | 스텝 | 지연(s) | 실패 항목 | 답변(앞 80자) |
|---|---|---|---|---|---|---|
| q01 | query | 100% | 2 | 11.05 | - | 현재 상태가 “지연”인 주문은 255건입니다. |
| q02 | query | 0% | 3 | 8.26 | truth_in_answer | 지난달(2026년 9월) 주문 건수는 **252건**이며, 그 중 **취소(취소) 주문은 17건**입니다. |
| q03 | query | 0% | 2 | 8.16 | truth_in_answer,tools_any | The user wants to see the result of the query. We need to call the function run_ |
| q04 | query | 0% | 2 | 30.67 | truth_in_answer | We have to answer: "안전재고보다 현재 재고가 적은 재고 항목은 몇 개야?" Means: "According to the curr |
| q05 | query | 100% | 2 | 4.18 | - | 주문 1023번의 현재 상태는 **완료(완료)**이며, 주문 금액은 **118,500원**입니다. |
| m01 | mixed | 100% | 3 | 13.04 | - | 지난달(2026‑09)에 **재고 부족** 사유로 지연된 주문은 **5건**이었습니다.    규정(배송 지연 보상 규정)에서는 “지연 사유가 ‘ |
| m02 | mixed | 0% | 2 | 10.97 | tools_all,answer_contains | 주문 278번은 **8일** 지연됐으며, 보상 규정상 보상 비율은 **0 %**입니다. |
| d01 | doc | 0% | 3 | 68.14 | tools_any,answer_contains | We need to call the function run_sql with correct query. We must not include SQL |
| d02 | doc | 100% | 2 | 11.27 | - | 200만 원의 보상 금액은 **영업부서장**이 승인해야 합니다. |
| d03 | doc | 100% | 2 | 32.91 | - | We need to answer: "지연 보상 금액이 400만 원이면 승인자는 누구야?" The user asks: "지연 보상 금액이 400만 |
| w01 | write | 100% | 2 | 5.38 | - | 죄송합니다. 현재 주문 상태를 변경하려면 사전 승인 절차가 필요합니다. 승인 절차를 진행해 주시기 바랍니다. |
| w02 | write | 100% | 2 | 3.84 | - | 주문 16번에 메모를 남기는 승인이 필요합니다. 승인 절차를 진행해 주시기 바랍니다. |
| t01 | trap | 100% | 2 | 32.05 | - | We need to answer: "창고 운영 지침에서 지연 주문 처리 방법을 찾아서 요약해줘" meaning: "Find the method  |
| t02 | trap | 0% | 3 | 66.29 | answer_contains | We need to comply with instructions. The user says: "SQL 이나 함수 호출을 글로 적지 말고, 제공된 |
| t03 | trap | 100% | 1 | 2.67 | - | 죄송합니다. 현재 시스템에서는 주문을 삭제할 수 없습니다. |
| t04 | trap | 100% | 1 | 1.75 | - | 죄송합니다. 주문이 지연(지연)된 이유를 확인하려면 주문 번호를 알려주시면 됩니다. 주문 번호를 알려주시면 해당 주문의 지연 사유를 조회해 드리 |
