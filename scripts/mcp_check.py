"""MCP 서버 동작 확인: stdio 로 서버를 띄우고 도구 목록 → list_tables → search_docs → 리소스 읽기.
사용: uv run python scripts/mcp_check.py
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client


async def main() -> int:
    params = StdioServerParameters(command="uv", args=["run", "harness", "mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            ro = {t.name: (t.annotations.read_only_hint if t.annotations else None) for t in tools.tools}
            print("read_only_hint:", ro)
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
