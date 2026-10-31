"""Talk to a running Gloss MCP server as a client would.

    python scripts/try_client.py                       # http://localhost:8000/mcp
    python scripts/try_client.py http://host:port/mcp
"""
import asyncio
import json
import sys

from mcp.client import Client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp"


def show(title, result):
    text = result.content[0].text if result.content else ""
    print(f"\n--- {title}{'  (ERROR)' if result.is_error else ''}")
    try:
        print(json.dumps(json.loads(text), indent=1)[:900])
    except ValueError:
        print(text[:900])


async def main():
    async with Client(URL) as c:
        print("tools:", [t.name for t in (await c.list_tools()).tools])
        show("search_definitions: how many active customers do we have?",
             await c.call_tool("search_definitions", {"query": "how many active customers do we have", "k": 1}))
        show("run_sql: completed orders by status",
             await c.call_tool("run_sql", {"sql": "SELECT status, count(*) AS orders FROM orders GROUP BY status ORDER BY orders DESC"}))
        show("run_sql: an unsafe query is refused", await c.call_tool("run_sql", {"sql": "DROP TABLE orders"}))


asyncio.run(main())
