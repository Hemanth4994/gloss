---
title: Gloss
sdk: docker
app_port: 7860
pinned: false
short_description: MCP server and LangGraph agent that answer data questions with your metric definitions
---

# Gloss

An open-source MCP server and a LangGraph agent that answer questions about a data warehouse using the company's own metric
definitions, with a measured evaluation of when the agent is right, wrong, or should refuse.

- **Tools tab** needs no key: search the metric definitions and run a guarded, read-only SELECT.
- **Ask the agent** retrieves definitions, writes one SQL query and answers with the SQL and the definitions it used. Paste your own
  free Gemini key, or use the small shared daily budget if the owner set one.
- **MCP endpoint**: this Space's address plus `/mcp` (streamable HTTP), for any MCP client.

The warehouse is synthetic. Code, evaluation results and limits: https://github.com/Hemanth4994/gloss
