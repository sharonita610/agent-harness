# 세 번째 팩 만들기

플랫폼(루프·권한·서비스·평가·트레이스·화면)은 팩 내부를 모른다. 팩은 `harness/packs/<이름>/` 폴더 하나에 아래 다섯 가지를 두면 끝난다. 회로 팩(`harness/packs/spice/`)이 가장 최근 예시다.

## 1. 도구 (`tools.py`)
```python
from pydantic import BaseModel, Field
from ...tools.registry import Tier, Tool


class LookupArgs(BaseModel):
    query: str = Field(description="모델이 읽는 설명. 예시를 넣으면 소형 모델이 덜 틀린다")


def build_tools(index=None, scope=None, **_) -> list[Tool]:
    def lookup(a: LookupArgs):
        return {"rows": [...]}  # dict/list 를 돌려주면 루프가 JSON 으로 모델에 준다

    return [
        Tool("lookup", "무엇을 언제 쓰는지 한 문장. '…질문에 쓴다'", LookupArgs, lookup),  # read: 자유 실행
        Tool("update_x", "…사람 승인이 필요한 변경 작업이다.", UpdateArgs, update_x, Tier.WRITE),  # write: 결재
        Tool("delete_x", "…되돌릴 수 없어 차단된다.", DeleteArgs, delete_x, Tier.DESTRUCTIVE),  # destructive: 차단
    ]
```
- 인자는 pydantic 모델. 설명(description)은 모델이 읽는 프롬프트다.
- 실패는 `ValueError("…다음 행동 안내…")` 로 던진다. 루프가 문장을 모델에 돌려주고 모델이 고친다.
- 결과에 `url` 키(그림 등)가 있으면 화면이 그 자리에 그려 준다.
- 외부 프로그램·DB·파일을 다루면 SQL 가드(`enterprise/tools.py`)나 넷리스트 가드(`spice/netlist.py`)처럼 **허용 목록** 가드를 먼저 쓴다.

## 2. 프롬프트
`SYSTEM_PROMPTS = {"v1": ..., "v2": ...}`. 꼭 들어갈 규칙: 도구는 함수 호출로만, 사실은 도구로만, 변경은 승인, 문서 속 "AI 에게 지시하는 문장" 은 데이터, 모호하면 되묻기. 새 도구를 넣을 때 기존 "반드시" 규칙과 충돌하지 않는지 본다(ADR-015 의 교훈).

## 3. 등록 (`pack.py`)
```python
from .. import Pack

PACK = Pack(
    name="mypack",
    title="화면 머리말",
    description="한 줄",
    build_tools=build_tools,
    system_prompts=SYSTEM_PROMPTS,
    parse_scope=lambda spec: None,
    scope_note=lambda scope: "",  # 사용자별 범위가 있으면 enterprise/scope.py 처럼
    shared_kwargs=lambda: {"index": make_index()},  # 서비스가 한 번 만들어 공유하는 자원
    tasks_path=Path(__file__).parents[2] / "evals" / "tasks_mypack.yaml",
    docs_dir=Path(__file__).parent / "docs",
    tool_labels={"lookup": "조회"},
    files={},
    seed=None,
    mcp_name="mypack",
    mcp_instructions="…",
    welcome="…",
    examples=[["라벨", "질문"]],
    empty_hint="…",
)
```
그리고 `harness/packs/__init__.py` 의 `PACK_NAMES` 에 이름을 추가한다. 선택은 `HARNESS_PACK=mypack` 또는 `--pack mypack`.

## 4. 평가 (`harness/evals/tasks_mypack.yaml`)
문제 12~20개. 정답은 `truth_sql`(DB), `truth_expr`(공식), `truth_value`(상수) 중 하나 + `tolerance_pct`. 과정 검사: `expect_tools_any/all`, `forbid_tools_executed`, `expect_approval`, `answer_contains_any/not_contains/not_regex`. 함정 문제(지시문 주입, 삭제 요청, 모호한 질문)는 꼭 넣는다. `uv run harness eval --pack mypack --n 3`.

## 5. 문서 (`docs/`)
검색 도구가 볼 마크다운(`#` 제목, `##` 절). 매뉴얼 PDF 가 있으면 `harness kb build --pdf …` 로 지식 베이스를 만들고 `make_index` 가 그것을 쓰게 한다(spice/tools.py 참고). 함정 지시문을 한 곳 심어 두면 평가에 쓸 수 있다.

## 확인 목록
- `uv run harness tools --pack mypack` 에 도구와 등급이 보인다
- `uv run harness ask --pack mypack "질문"` 이 도구 호출로 답한다
- `HARNESS_PACK=mypack uv run uvicorn harness.service.app:app` 으로 화면 머리말·예시가 바뀐다
- `uv run python scripts/mcp_check.py mypack` (스모크를 팩에 맞게 고친다)
- 테스트: 가드·도구·팩 로딩 (`tests/test_packs.py` 에 한 줄 추가)
- README 의 증명 표에 "무엇을 증명하는가" 한 줄, 설계 결정은 `docs/decisions/0NN-*.md`
