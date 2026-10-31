"""supervisor 팩 등록: 총괄 비서(A2A 라우터). 하위 비서 주소는 HARNESS_A2A_AGENTS(쉼표 구분)."""

from __future__ import annotations

from .. import Pack
from .dashboard import dashboard
from .tools import DOCS_DIR, SYSTEM_PROMPTS, TOOL_LABELS, build_tools, scope_note

PACK = Pack(
    name="supervisor",
    title="총괄 비서 (A2A)",
    description="분야별 비서 3개를 A2A 프로토콜로 호출해 질문을 위임하고 답을 합치는 감독 에이전트. 독립 질의는 delegate_many 로 동시에 보낸다. 도구 5개.",
    build_tools=build_tools,
    system_prompts=SYSTEM_PROMPTS,
    parse_scope=lambda spec: None,
    scope_note=scope_note,
    shared_kwargs=lambda: {},
    tasks_path=DOCS_DIR.parents[2] / "evals" / "tasks_supervisor.yaml",
    docs_dir=DOCS_DIR,
    tool_labels=TOOL_LABELS,
    mcp_name="supervisor",
    mcp_instructions="분야별 비서에게 A2A 로 위임하는 총괄 비서.",
    dashboard=dashboard,
    welcome="회사 데이터·회로 시뮬레이션·공정 NG 비서에게 질문을 나눠 보내고 답을 합칩니다. 하위 비서의 결재 요청은 오른쪽 결재함에 모입니다.",
    examples=[
        [
            "회사 + 회로",
            "현재 지연 주문이 몇 건인지와, 저항 1kΩ·커패시터 100nF RC 저역통과 필터의 차단 주파수를 함께 알려줘",
        ],
        ["공정 급증", "지난 2주 동안 NG 율이 가장 크게 튄 라인과 스테이션은 어디고 언제였어?"],
        ["규정", "배송사 지연으로 7일 늦어진 주문의 보상 비율은?"],
        ["결재 필요", "주문 353번에 '총괄 비서 확인' 메모 남겨줘"],
    ],
    empty_hint="질문을 보내면 총괄 비서가 어느 비서에게 위임했는지와 그 답을 순서대로 보여줍니다.",
)
