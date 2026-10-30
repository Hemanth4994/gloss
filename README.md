# Gloss

Ask your data, grounded in your definitions. An open-source MCP server and a LangGraph agent for a data warehouse, with a
measured evaluation of when the agent is right, wrong, or should refuse.

**Status: planning and first components. There are no results yet.** See [PLAN.md](PLAN.md).

## What exists

- `src/gloss/sqlguard.py`: static checks on model-written SQL (one SELECT, named allow-listed tables, no table functions).
- `src/gloss/warehouse.py`: read-only DuckDB connection with no file or network access, a row cap and a timeout.
- 21 unit tests: `PYTHONPATH=src python -m unittest discover -s tests`

## Sources used when planning

- Dataquest, [Best AI Projects to Build in 2026](https://www.dataquest.io/blog/ai-projects/) (July 2026)
- Landed, [projects to land an AI job](https://github.com/landedjobs/projects-to-land-an-ai-job)
- CData, [MCP server best practices 2026](https://www.cdata.com/blog/mcp-server-best-practices-2026)
- KDnuggets, [5 free ways to host a Python application](https://www.kdnuggets.com/5-free-ways-to-host-a-python-application) (April 2026)
