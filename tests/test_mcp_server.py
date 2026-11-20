"""The MCP server exposes the same tools, resources, and prompts (in-memory MCP session)."""

import asyncio
import json

from mcp import Client

from medicaidiq.mcp_server import mcp


def test_mcp_lists_and_calls_tools():
    async def go():
        async with Client(mcp) as session:
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"run_sql", "pre_post_analysis", "get_policy_timeline"} <= names
            r = await session.call_tool("get_policy_timeline", {"state": "EV", "topic": "glp1_prior_auth"})
            payload = json.loads(r.content[0].text)
            assert payload["value"] == "2024-07-01"
            r = await session.call_tool("run_sql", {"sql": "DROP TABLE states"})
            assert "error" in json.loads(r.content[0].text)
            schema = await session.read_resource("schema://medicaidiq")
            assert "drug_utilization" in schema.contents[0].text
            doc = await session.read_resource("policy://EV-BUL-2024-011")
            assert "postponed" in doc.contents[0].text.lower()
            prompts = {p.name for p in (await session.list_prompts()).prompts}
            assert "pre_post_policy_analysis" in prompts
    asyncio.run(go())
