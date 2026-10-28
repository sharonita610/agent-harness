# LangGraph 비교 실험 · spice · 처음 8문제 · 로컬 gpt-oss-20b

통과 8/8 · 평균 스텝 3.12 · 평균 지연 17.7s

| id | 유형 | 결과 | 스텝 | 지연 | 도구 | 실패 항목 / 오류 |
|---|---|---|---|---|---|---|
| s01 | sim | ✅ | 2 | 12.8s | run_simulation |   |
| s02 | sim | ✅ | 4 | 10.5s | list_circuits → get_circuit → run_simulation |   |
| s03 | sim | ✅ | 2 | 8.5s | run_simulation |   |
| s04 | sim | ✅ | 2 | 9.0s | run_simulation |   |
| s05 | sim | ✅ | 4 | 20.7s | list_circuits → get_circuit → run_simulation |   |
| s06 | sim | ✅ | 2 | 9.8s | run_simulation |   |
| s07 | sim | ✅ | 4 | 13.2s | list_circuits → get_circuit → run_simulation |   |
| s08 | sim | ✅ | 5 | 56.8s | search_manual → list_circuits → get_circuit → run_simulation |   |
