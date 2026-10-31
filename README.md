# Gloss

Ask your data, grounded in your definitions. An open-source MCP server and a LangGraph agent for a data warehouse, with a
measured evaluation of when the agent is right, wrong, or should refuse.

**Status: planning and first components. There are no results yet.** See [PLAN.md](PLAN.md).

## What exists

- `src/gloss/datagen.py`: synthetic online-store data with a fixed seed and planted traps (test accounts, cancelled and returned orders, refunds in their own table, UTC timestamps reported in IST) plus a known number of injected dirty rows.
- `src/gloss/pipeline.py`: loads the rows into DuckDB with documented quality rules and writes a data-quality report. A test checks that the report matches exactly what was injected.
- `src/gloss/dictionary.yaml`: rules, table descriptions and nine metric definitions. Each metric has reference SQL, used only by tests and gold answers, never shown to the agent.
- `src/gloss/sqlguard.py` and `warehouse.py`: static checks on model-written SQL, then a read-only connection with no file or network access, a row cap and a timeout.
- `src/gloss/retrieval.py`: BM25 over the dictionary chunks (pure Python). Embeddings only if the retrieval evaluation shows a need.
- `src/gloss/tools.py` and `mcp_server.py`: four read-only tools (`list_tables`, `describe_table`, `search_definitions`, `run_sql`) served over MCP with the official Python SDK (v2), over stdio or streamable HTTP. A refused or failed query returns a tool error the model can read.
- 58 tests: `PYTHONPATH=src:tests python -m unittest discover -s tests`. They include a check of every metric's SQL against an independent pure-Python calculation, a check that each planted trap really changes the answer, and calls through a real in-process MCP client.

Build the warehouse, then serve it:

```
PYTHONPATH=src python -m gloss.pipeline --out data/gloss.duckdb
PYTHONPATH=src python -m gloss.mcp_server --db data/gloss.duckdb              # stdio (Claude Desktop, MCP Inspector)
PYTHONPATH=src python -m gloss.mcp_server --db data/gloss.duckdb --http       # streamable HTTP at http://localhost:8000/mcp
```

## Sources used when planning

- Dataquest, [Best AI Projects to Build in 2026](https://www.dataquest.io/blog/ai-projects/) (July 2026)
- Landed, [projects to land an AI job](https://github.com/landedjobs/projects-to-land-an-ai-job)
- CData, [MCP server best practices 2026](https://www.cdata.com/blog/mcp-server-best-practices-2026)
- KDnuggets, [5 free ways to host a Python application](https://www.kdnuggets.com/5-free-ways-to-host-a-python-application) (April 2026)
