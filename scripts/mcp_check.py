"""MCP 서버 동작 확인: stdio 로 서버를 띄우고 도구 목록 → 조회 도구 → 검색 → 리소스 읽기.
사용: uv run python scripts/mcp_check.py            (enterprise 팩)
      uv run python scripts/mcp_check.py spice      (회로 팩: list_circuits → run_simulation → search_manual)
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client


async def check_spice(s, names: list[str]) -> None:
    r = await s.call_tool("list_circuits", {})
    print("list_circuits ->", r.content[0].text[:120])
    r = await s.call_tool(
        "run_simulation",
        {
            "netlist": "* RC\nV1 in 0 dc 0 ac 1\nR1 in out 1k\nC1 out 0 100n\n.ac dec 20 10 1meg",
            "measurements": ["fc WHEN vdb(out)=-3"],
        },
    )
    print("run_simulation ->", json.loads(r.content[0].text)["measurements"])
    r = await s.call_tool(
        "run_simulation", {"netlist": "* RC\n.include /etc/passwd\nR1 in 0 1k\n.op", "measurements": []}
    )
    print("run_simulation(.include) -> isError:", r.is_error, "|", r.content[0].text[:80])
    r = await s.call_tool("search_manual", {"query": "차단 주파수 공식", "k": 2})
    hits = [json.loads(c.text) for c in r.content]  # 목록 결과는 항목마다 content 하나
    print("search_manual ->", [h["source"] for h in hits])
    doc = await s.read_resource("docs://05-해석-공식")
    print("read_resource ->", doc.contents[0].text[:40].replace("\n", " "))
    assert "delete_circuit" not in names, "destructive 도구가 노출됨"


async def check_factory(s, names: list[str]) -> None:
    r = await s.call_tool("detect_spikes", {"limit": 2})
    top = json.loads(r.content[0].text)["spikes"][0]
    print("detect_spikes ->", top["station"], top["start"], top["multiplier"])
    r = await s.call_tool("search_docs", {"query": "급증 알림 기준", "k": 2})
    hits = [json.loads(c.text) for c in r.content]
    print("search_docs ->", [h["source"] for h in hits])
    r = await s.call_tool("run_sql", {"sql": "DELETE FROM ng_events"})
    print("run_sql(DELETE) -> isError:", r.is_error)
    doc = await s.read_resource("docs://05-급증-알림-기준")
    print("read_resource ->", doc.contents[0].text[:30].replace("\n", " "))
    assert "delete_ng_events" not in names, "destructive 도구가 노출됨"


async def main() -> int:
    pack = sys.argv[1] if len(sys.argv) > 1 else "enterprise"
    params = StdioServerParameters(command="uv", args=["run", "harness", "mcp", "--pack", pack])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            ro = {t.name: (t.annotations.read_only_hint if t.annotations else None) for t in tools.tools}
            print("read_only_hint:", ro)
            if pack == "spice":
                await check_spice(s, names)
                print("MCP CHECK OK (spice)")
                return 0
            if pack == "factory":
                await check_factory(s, names)
                print("MCP CHECK OK (factory)")
                return 0
            r = await s.call_tool("list_tables", {})
            print("list_tables ->", r.content[0].text[:160])
            r = await s.call_tool("search_docs", {"query": "할인 승인자", "k": 2})
            hits = json.loads(r.content[0].text)["hits"]
            print("search_docs ->", [h["source"] for h in hits])
            r = await s.call_tool("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders WHERE status='지연'"})
            print("run_sql ->", r.content[0].text[:120])
            r = await s.call_tool("run_sql", {"sql": "DELETE FROM orders"})
            print("run_sql(DELETE) -> isError:", r.is_error, "|", r.content[0].text[:100])
            res = await s.list_resources()
            print("resources:", [str(x.uri) for x in res.resources][:3], "(템플릿은 list_resource_templates)")
            doc = await s.read_resource("docs://03-할인-승인-규정")
            print("read_resource ->", doc.contents[0].text[:60].replace("\n", " "))
            assert "delete_order" not in names, "destructive 도구가 노출됨"
            print("MCP CHECK OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
