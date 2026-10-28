# LangGraph 비교 실험 · enterprise · 처음 6문제 · 로컬 gpt-oss-20b

통과 6/6 · 평균 스텝 2.50 · 평균 지연 6.0s

| id | 유형 | 결과 | 스텝 | 지연 | 도구 | 실패 항목 / 오류 |
|---|---|---|---|---|---|---|
| q01 | query | ✅ | 2 | 3.9s | run_sql |   |
| q02 | query | ✅ | 2 | 6.8s | run_sql |   |
| q03 | query | ✅ | 3 | 5.5s | list_tables → run_sql |   |
| q04 | query | ✅ | 2 | 3.2s | run_sql |   |
| q05 | query | ✅ | 2 | 4.4s | get_order |   |
| m01 | mixed | ✅ | 4 | 12.3s | list_tables → run_sql → search_docs |   |
