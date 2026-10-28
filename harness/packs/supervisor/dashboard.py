"""총괄 비서의 "데이터" 탭: 하위 비서들의 연결·도구·모델·대기 결재 현황."""
from __future__ import annotations

import os
from typing import Any

from .tools import DEFAULT_AGENTS


def _get(http: Any, url: str, headers: dict[str, str]) -> dict[str, Any]:
    if http is None:
        import httpx
        r = httpx.get(url, headers=headers, timeout=3.0)
    else:
        r = http.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


def dashboard(scope: Any = None, agents: list[str] | None = None, api_key: str | None = None, http: Any = None) -> list[dict[str, Any]]:
    urls = agents or [u.strip().rstrip("/") for u in os.getenv("HARNESS_A2A_AGENTS", DEFAULT_AGENTS).split(",") if u.strip()]
    api_key = api_key or os.getenv("HARNESS_A2A_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    table, ok, tools, pending = [], 0, 0, 0
    for u in urls:
        try:
            card = _get(http, u + "/.well-known/agent-card.json", headers)
            h = _get(http, u + "/health", headers)
            m = h.get("model") or {}
            model = f"{h.get('provider', '?')} " + ("호스팅" if m.get("hosted") else ("켜짐" if m.get("reachable") else "꺼짐"))
            ok += 1; tools += int(h.get("tools", 0)); pending += int(h.get("pending_approvals", 0))
            table.append({"비서": card.get("name", u), "팩": card.get("metadata", {}).get("pack", ""), "연결": "연결됨", "도구": h.get("tools", 0), "모델": model,
                          "대기 결재": h.get("pending_approvals", 0), "기술": ", ".join(s.get("name", "") for s in card.get("skills", [])[:4]), "주소": u})
        except Exception as e:
            table.append({"비서": u, "팩": "", "연결": f"끊김 ({type(e).__name__})", "도구": "", "모델": "", "대기 결재": "", "기술": "", "주소": u})
    return [
        {"id": "stats", "title": "한눈에", "kind": "stats", "span": 2, "data": [
            {"label": "연결된 비서", "value": f"{ok}/{len(urls)}", "tone": "ok" if ok == len(urls) else "bad"},
            {"label": "도구 합계", "value": tools, "unit": "개"},
            {"label": "대기 결재", "value": pending, "unit": "건", "tone": "warn" if pending else "ok"},
        ]},
        {"id": "agents", "title": "비서 현황", "kind": "table", "span": 2, "data": {"columns": ["비서", "팩", "연결", "도구", "모델", "대기 결재", "기술", "주소"], "rows": table},
         "note": "총괄 비서는 이 비서들에게 A2A 로 질문을 위임한다. 끊긴 비서는 위임에서 제외된다."},
    ]
