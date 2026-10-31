"""Gloss as an MCP server (official Python SDK, v2).

    python -m gloss.mcp_server --db data/gloss.duckdb                     # stdio: Claude Desktop, MCP Inspector, other clients
    python -m gloss.mcp_server --db data/gloss.duckdb --http --port 8000  # streamable HTTP at /mcp

Tools are read-only. Anything the SQL guard or the database refuses comes back as a tool error whose message the model can read and act on.
"""
from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import tools
from .warehouse import GuardError, QueryError, QueryTimeout

INSTRUCTIONS = (
    "Read-only access to a synthetic online-store warehouse. Business metrics have precise definitions "
    "(test accounts, order status, IST dates, refunds). Before writing SQL for any metric or date question, call "
    "search_definitions and follow what it returns. Use list_tables and describe_table to see the schema. "
    "run_sql accepts one SELECT over the listed tables only. If the data cannot answer the question, say so."
)


def build_server(wh: tools.Warehouse) -> MCPServer:
    server = MCPServer("gloss", instructions=INSTRUCTIONS)

    def guarded(fn, *args):
        try:
            return fn(wh, *args)
        except (GuardError, QueryError, QueryTimeout, KeyError, ValueError) as exc:
            raise ToolError(str(exc).strip("'\"")) from exc

    @server.tool()
    def list_tables() -> list[dict]:
        """List the tables you can query, with a one-line description of each."""
        return guarded(tools.list_tables)

    @server.tool()
    def describe_table(table: str) -> dict:
        """Columns, types, column descriptions and three sample rows for one table."""
        return guarded(tools.describe_table, table)

    @server.tool()
    def search_definitions(query: str, k: int = 3) -> list[dict]:
        """Find the business definitions, rules and table notes most relevant to a question (BM25 over the data dictionary)."""
        return guarded(tools.search_definitions, query, k)

    @server.tool()
    def run_sql(sql: str) -> dict:
        """Run one read-only SELECT query (DuckDB SQL). At most 200 rows are returned and long queries are stopped."""
        return guarded(tools.run_sql, sql)

    return server


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("GLOSS_DB", "data/gloss.duckdb"))
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    server = build_server(tools.Warehouse.open(a.db))
    if a.http:
        import uvicorn
        uvicorn.run(server.streamable_http_app(host="0.0.0.0"), host="0.0.0.0", port=a.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
