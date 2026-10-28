"""총괄 비서(감독 에이전트): 다른 팩 비서들을 A2A 프로토콜로 호출해 질문을 위임하고 답을 합친다.

하위 비서는 각각 독립 서비스(FastAPI + /a2a)이고, 이 팩은 그 Agent Card 를 읽어 도구(list_agents / delegate / check_task)로 노출한다.
멀티에이전트 = 같은 플랫폼의 비서들이 A2A 로 서로 부르는 것. 사람 결재는 하위 비서 쪽에서 그대로 유지된다(input-required).
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...retrieval import DocIndex
from ...tools.registry import Tool

PACK_DIR = Path(__file__).parent
DOCS_DIR = PACK_DIR / "docs"
DEFAULT_AGENTS = "http://127.0.0.1:8786,http://127.0.0.1:8788,http://127.0.0.1:8789"


class A2AClient:
    """A2A JSON-RPC 클라이언트(httpx). 테스트에선 TestClient 를 http 로 넘겨 쓴다."""

    def __init__(self, base_url: str, api_key: str | None = None, http: Any = None, timeout: float = 200.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        if http is None:
            import httpx
            http = httpx.Client(timeout=timeout)
        self.http = http
        self._card: dict[str, Any] | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def card(self) -> dict[str, Any]:
        if self._card is None:
            r = self.http.get(f"{self.base_url}/.well-known/agent-card.json", headers=self._headers())
            if r.status_code == 404:
                r = self.http.get(f"{self.base_url}/.well-known/agent.json", headers=self._headers())
            r.raise_for_status()
            self._card = r.json()
        return self._card

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        url = self.card().get("url") or f"{self.base_url}/a2a"
        r = self.http.post(url, json={"jsonrpc": "2.0", "id": "rpc_" + uuid.uuid4().hex[:8], "method": method, "params": params}, headers=self._headers())
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"A2A 오류 {body['error'].get('code')}: {body['error'].get('message')}")
        return body["result"]

    def send(self, text: str, context_id: str | None = None, blocking: bool = True, timeout_s: int = 180) -> dict[str, Any]:
        msg = {"kind": "message", "role": "user", "messageId": "msg_" + uuid.uuid4().hex[:12], "parts": [{"kind": "text", "text": text}]}
        if context_id:
            msg["contextId"] = context_id
        return self.rpc("message/send", {"message": msg, "configuration": {"blocking": blocking, "timeoutSeconds": timeout_s, "acceptedOutputModes": ["text/plain"]}})

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.rpc("tasks/get", {"id": task_id})


def task_text(task: dict[str, Any]) -> str:
    texts = []
    for art in task.get("artifacts") or []:
        for p in art.get("parts") or []:
            if p.get("kind") == "text" and p.get("text"):
                texts.append(p["text"])
            elif p.get("kind") == "file":
                texts.append(f"[파일] {p.get('file', {}).get('uri')}")
    return "\n".join(texts).strip()


# ----------------------------------------------------------------------------- 인자
class NoArgs(BaseModel):
    pass


class DelegateArgs(BaseModel):
    agent: str = Field(description="보낼 비서. list_agents 의 id 또는 이름 일부(예: '회사', '회로', '공정')")
    question: str = Field(description="그 비서에게 보낼 질문. 필요한 조건(기간·값·번호)을 빠뜨리지 말고 그대로 적는다")
    wait_s: int = Field(180, ge=5, le=600, description="답을 기다리는 최대 시간(초)")


class CheckTaskArgs(BaseModel):
    agent: str
    task_id: str


class SearchArgs(BaseModel):
    query: str
    k: int = Field(3, ge=1, le=10)


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "-", name.lower()).strip("-")


def build_tools(agents: list[str] | None = None, api_key: str | None = None, http: Any = None, index: DocIndex | None = None,
                scope: Any = None, **_: Any) -> list[Tool]:
    urls = agents or [u.strip() for u in os.getenv("HARNESS_A2A_AGENTS", DEFAULT_AGENTS).split(",") if u.strip()]
    api_key = api_key or os.getenv("HARNESS_A2A_API_KEY")
    clients = {u: A2AClient(u, api_key=api_key, http=http) for u in urls}
    index = index or DocIndex(DOCS_DIR)
    contexts: dict[str, str] = {}  # agent url → contextId(세션) 를 이어 쓴다

    def _registry() -> list[dict[str, Any]]:
        out = []
        for u, c in clients.items():
            try:
                card = c.card()
                out.append({"id": card.get("metadata", {}).get("pack") or _slug(card["name"]), "name": card["name"], "description": card.get("description", ""),
                            "aliases": card.get("metadata", {}).get("aliases", []), "url": u, "skills": [s["name"] for s in card.get("skills", [])][:6], "status": "ok"})
            except Exception as e:
                out.append({"id": _slug(u), "name": u, "description": "", "url": u, "skills": [], "status": f"연결 실패: {type(e).__name__}"})
        return out

    def _pick(agent: str) -> tuple[str, A2AClient]:
        key = agent.strip().lower()
        reg = _registry()
        for a in reg:
            if key in (a["id"], a["name"].lower(), a["url"]):
                return a["id"], clients[a["url"]]
        for a in reg:
            hay = [a["name"].lower(), a["id"], a["description"].lower(), *[x.lower() for x in a.get("aliases", [])]]
            if key and any(key in h or h in key for h in hay if h):
                return a["id"], clients[a["url"]]
        raise ValueError(f"비서를 찾지 못했습니다: {agent!r}. 가능한 비서: {[a['id'] + '(' + a['name'] + ')' for a in reg]}")

    def list_agents(_: NoArgs) -> Any:
        return {"agents": _registry(), "note": "delegate(agent=id, question=...) 로 위임한다"}

    def delegate(a: DelegateArgs) -> Any:
        aid, client = _pick(a.agent)
        task = client.send(a.question, context_id=contexts.get(client.base_url), blocking=True, timeout_s=a.wait_s)
        contexts[client.base_url] = task.get("contextId") or contexts.get(client.base_url, "")
        state = task.get("status", {}).get("state")
        out = {"agent": aid, "task_id": task.get("id"), "state": state, "answer": task_text(task)}
        if state == "input-required":
            msg = task.get("status", {}).get("message") or {}
            out["waiting_for"] = "\n".join(p.get("text", "") for p in msg.get("parts", []))
            out["note"] = "하위 비서가 사람 결재를 기다리는 중이다. 완료된 것처럼 답하지 말고 '결재 대기' 로 보고한다. check_task 로 나중에 확인할 수 있다."
        elif state == "failed":
            out["note"] = "하위 비서 실행이 실패했다: " + "\n".join(p.get("text", "") for p in (task.get("status", {}).get("message") or {}).get("parts", []))
        elif state not in ("completed",):
            out["note"] = f"아직 끝나지 않았다(state={state}). check_task(agent, task_id) 로 확인한다."
        return out

    def check_task(a: CheckTaskArgs) -> Any:
        aid, client = _pick(a.agent)
        task = client.get_task(a.task_id)
        return {"agent": aid, "task_id": task.get("id"), "state": task.get("status", {}).get("state"), "answer": task_text(task)}

    def search_rules(a: SearchArgs) -> Any:
        return index.search(a.query, k=a.k)

    return [
        Tool("list_agents", "위임할 수 있는 비서 목록(A2A Agent Card)을 본다. 어느 비서에게 보낼지 모르겠으면 먼저 호출한다.", NoArgs, list_agents),
        Tool("delegate", "질문을 한 비서에게 A2A 로 보내고 답을 받는다. 분야가 섞인 질문은 나눠서 여러 번 부른다. 결과의 answer 를 인용하고 숫자를 바꾸지 않는다.", DelegateArgs, delegate),
        Tool("check_task", "이전에 위임한 태스크의 현재 상태와 답을 확인한다(결재 대기였던 경우).", CheckTaskArgs, check_task),
        Tool("search_rules", "위임 규칙 문서를 검색한다.", SearchArgs, search_rules),
    ]


SYSTEM_PROMPT = """당신은 총괄 비서입니다. 직접 데이터를 조회하지 않고, 분야별 비서(회사 데이터 비서 · 회로 시뮬레이션 비서 · 공정 NG 분석 비서)에게 A2A 로 질문을 위임해 답을 받아 합칩니다.

원칙
0. 도구는 반드시 함수 호출(tool call)로 실행합니다. 계획만 말하고 끝내지 않습니다.
1. 어느 비서에게 보낼지는 질문의 분야로 정합니다: 주문·재고·거래처·규정 → 회사 데이터 비서, 회로·시뮬레이션·차단 주파수·매뉴얼 → 회로 시뮬레이션 비서, 공정·라인·NG·불량·설비 → 공정 NG 분석 비서. 모르겠으면 list_agents 로 목록을 봅니다.
2. 두 분야가 섞인 질문은 나눠서 각각 delegate 하고, 답을 합칠 때 어느 비서의 답인지 밝힙니다.
3. 위임 결과의 숫자와 결론을 바꾸지 않습니다. 비서가 되물으면 그 질문을 사용자에게 그대로 전달합니다. 비서가 거절했으면 거절 사유를 전달합니다.
4. 하위 비서가 사람 결재를 기다리면(state=input-required) "결재 대기" 로 보고하고, 완료됐다고 말하지 않습니다.
5. 질문이 모호하면(주문 번호·회로 값·라인이 없음) 위임하기 전에 사용자에게 되묻습니다.
6. 답은 한국어로 간결하게. 첫 문장에 결론, 그다음 "출처: 어느 비서".
"""
SYSTEM_PROMPTS = {"v1": SYSTEM_PROMPT, "v2": SYSTEM_PROMPT}
TOOL_LABELS = {"list_agents": "비서 목록 확인", "delegate": "비서에게 위임", "check_task": "위임 결과 확인", "search_rules": "위임 규칙 검색"}


def scope_note(scope: Any) -> str:
    return ""
