"""Hugging Face Tiny Agents(huggingface_hub[mcp]) 로 우리 MCP 서버를 돌려 보는 실험. 대화형 CLI 대신 질문 하나를 넣고 끝낸다.

사용: uv tool install "huggingface_hub[mcp]"   (tiny-agents 실행 파일과 같은 환경)
      ~/.local/share/uv/tools/huggingface-hub/bin/python docs/experiments/tiny-agents/run.py "질문" [enterprise|spice]
서버: scripts/serve_llm.sh 가 8080 에 떠 있어야 한다.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from huggingface_hub import Agent

HERE = Path(__file__).parent
ROOT = HERE.resolve().parents[2]


async def main(question: str, pack: str) -> None:
    cfg = json.loads((HERE / "agent.json").read_text(encoding="utf-8"))
    servers = cfg["servers"]
    for s in servers:
        s["args"] = [a.replace("/Users/sharonkim/IdeaProjects/local-agent-lab", str(ROOT)) for a in s["args"]] + (["--pack", pack] if pack != "enterprise" else [])
        s["cwd"] = str(ROOT)
    prompt = (HERE / "PROMPT.md").read_text(encoding="utf-8") if pack == "enterprise" else "당신은 회로 시뮬레이션 비서입니다. 숫자는 run_simulation 으로 재고, 문법은 search_manual 로 찾습니다. 한국어로 간결하게 답합니다."
    agent = Agent(model=cfg["model"], base_url=cfg["endpointUrl"], api_key=cfg.get("apiKey"), servers=servers, prompt=prompt)
    t0 = time.perf_counter()
    async with agent:
        await agent.load_tools()
        print(f"[tiny-agents] 도구 {len(agent.available_tools)}개: {[t.function.name for t in agent.available_tools]}")
        text = []
        calls = 0
        async for item in agent.run(question):
            if isinstance(item, dict) and item.get("role") == "tool":
                calls += 1
                print(f"[tool #{calls}] {item.get('name')}: {str(item.get('content'))[:160]}")
            elif hasattr(item, "choices") and item.choices:
                d = item.choices[0].delta
                if getattr(d, "content", None):
                    text.append(d.content)
                for tc in getattr(d, "tool_calls", None) or []:
                    if getattr(tc.function, "name", None):
                        print(f"[call] {tc.function.name}", flush=True)
        print(f"\n[answer] {''.join(text).strip()}\n[tiny-agents] 도구 호출 {calls}회 · {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "현재 상태가 '지연'인 주문은 총 몇 건이야?", sys.argv[2] if len(sys.argv) > 2 else "enterprise"))
