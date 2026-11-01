"""민감도 등급표(열 단위). 실데이터를 붙일 때 가장 먼저 채우는 표다(README "기존 DB 를 붙일 때", ADR-035).

뷰 단계에서 적용된다(scope.apply_scope → tools.sql.build_views): 같은 이름의 임시 뷰가 열을 가리거나 빼므로
별칭(`phone AS tel`)·함수(`substr(phone, …)`)·조인·`group_concat` 어떤 SQL 로도 원문이 나가지 않는다.
등급: phone(국번과 뒤 4자리만) · email(도메인만) · full(값 전체 ***) · drop(열 자체를 뷰에서 뺀다, 조회 불가).
적지 않은 열은 그대로. 이름(customers.manager, order_notes.author)은 가리지 않는다(ADR-028: 업무 기록에 담당자 이름은 필요).
표의 테이블·열이 DB 에 없으면 연결이 실패한다(오타가 "안 가려진 채 통과" 로 끝나지 않게).
"""

SENSITIVITY: dict[str, dict[str, str]] = {
    "customers": {"phone": "phone"},
}
