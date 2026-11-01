# Gloss

Ask your data, grounded in your definitions. An open-source MCP server and a LangGraph agent for a data warehouse, with a
measured evaluation of when the agent is right, wrong, or should refuse.

**Status: data, definitions, MCP server, evaluation set and agent are built. The agent has been tested only with a scripted fake model; it has not been run against a real model yet, so there is no accuracy result.**
The only number so far is a retrieval check on the development questions (below). See [PLAN.md](PLAN.md).

## What exists

- `src/gloss/datagen.py`: synthetic online-store data with a fixed seed and planted traps (test accounts, cancelled and returned orders, refunds in their own table, UTC timestamps reported in IST) plus a known number of injected dirty rows.
- `src/gloss/pipeline.py`: loads the rows into DuckDB with documented quality rules and writes a data-quality report. A test checks that the report matches exactly what was injected.
- `src/gloss/dictionary.yaml`: rules, table descriptions and nine metric definitions. Each metric has reference SQL, used only by tests and gold answers, never shown to the agent.
- `src/gloss/sqlguard.py` and `warehouse.py`: static checks on model-written SQL, then a read-only connection with no file or network access, a row cap and a timeout.
- `src/gloss/retrieval.py`: BM25 over the dictionary chunks (pure Python). Embeddings only if the retrieval evaluation shows a need.
- `src/gloss/tools.py` and `mcp_server.py`: four read-only tools (`list_tables`, `describe_table`, `search_definitions`, `run_sql`) served over MCP with the official Python SDK (v2), over stdio or streamable HTTP. A refused or failed query returns a tool error the model can read.
- `eval/questions.yaml`, `src/gloss/evaluation.py`: 60 questions, 30 dev and 30 test, each split with 12 definition questions, 8 join questions, 5 raw-table controls and 5 questions the data cannot answer (the right behaviour is to refuse). Gold SQL is written with macros so each trap (`test` accounts, `tz` IST vs UTC, `status`, `refund`) can be switched off to give a plausible wrong query. Tests prove every listed trap changes the answer, and that nothing is listed that does not. `eval/gold_answers.json` is the stored answer key; `scripts/make_gold.py` rebuilds it.
- Scoring is execution accuracy: the result of the agent's final SQL is compared with the gold result (column names ignored, extra columns allowed, integers exact, other numbers within the stated decimals, row order only for top-N questions). A refusal is correct only on the unanswerable questions. A scorer test replays the gold SQL (100%), refuses everything, never refuses, and ignores every definition, and checks each lands where it should.
- `src/gloss/agent.py`: a LangGraph graph (retrieve, plan, execute, repair at most twice, then answer, refuse or fail) around the same tool functions the MCP server exposes. The model is injected as a plain function and returns structured output (`sql`, `definitions_used`, `assumptions`, `answerable`, `refusal_reason`). At most 3 model calls per question. The answer text is built from the query result and the cited definitions, not written by the model. Two configurations, `definitions` (all rules plus BM25 top 3) and `none` (bare schema only), share one system prompt that contains no business rule, and a test checks that. Each run carries LangSmith tags and metadata.
- `src/gloss/throttle.py`: request pacing for the free-tier rate limit, waits out per-minute 429s, stops cleanly on a daily quota.
- `scripts/run_eval.py`: runs a split in one configuration, scores it, resumes after an interruption, records the model that answered and hashes of the prompt, questions and answer key. The test split refuses to run unless the tag `frozen-v1` exists and `src/`, `eval/`, `scripts/` and `tests/` match it exactly.
- 114 tests: `PYTHONPATH=src:tests python -m unittest discover -s tests` (the agent tests need `pip install -e ".[agent]"` and are skipped without it). They include a check of every metric's SQL against an independent pure-Python calculation, a check that each planted trap really changes the answer, calls through a real in-process MCP client, and the evaluation-set checks above.

Build the warehouse, then serve it:

```
PYTHONPATH=src python -m gloss.pipeline --out data/gloss.duckdb
PYTHONPATH=src python -m gloss.mcp_server --db data/gloss.duckdb              # stdio (Claude Desktop, MCP Inspector)
PYTHONPATH=src python -m gloss.mcp_server --db data/gloss.duckdb --http       # streamable HTTP at http://localhost:8000/mcp
```

## Retrieval check (development questions only, no model involved)

`PYTHONPATH=src python scripts/eval_retrieval.py` asks: for each of the 20 development questions that need a definition, did the retriever bring back every rule and metric chunk the answer depends on? The query is the question text. Full output is in `results/retrieval_dev.json`.

| strategy | mean recall | questions with every needed chunk | rule recall | metric recall | avg words shown |
|---|---|---|---|---|---|
| BM25 top 1 | 0.271 | 0.00 | 0.083 | 0.625 | 48 |
| BM25 top 3 | 0.454 | 0.05 | 0.217 | 1.000 | 128 |
| BM25 top 5 | 0.471 | 0.05 | 0.242 | 1.000 | 213 |
| all rules + BM25 top 1 | 0.908 | 0.70 | 1.000 | 0.625 | 269 |
| all rules + BM25 top 3 | 1.000 | 1.00 | 1.000 | 1.000 | 325 |

What this says: plain BM25 finds the metric a question names ("net revenue", "repeat customers") but almost never the rules the question only implies ("exclude test accounts", "use IST dates"), because those words are not in the question. Showing every rule chunk plus the top 3 BM25 chunks fixes that on the development questions, at about 325 words of context. That is the setting I will use for the agent.

Caveats. Rule recall of 1.0 for the "all rules" rows is by construction, since every rule is always shown; it says nothing about whether the agent then applies them, which is what the agent evaluation measures. The questions and the dictionary were written by the same author, so this measures use of supplied definitions, not discovery of conventions. The test split has not been used for any choice.

## Sources used when planning

- Dataquest, [Best AI Projects to Build in 2026](https://www.dataquest.io/blog/ai-projects/) (July 2026)
- Landed, [projects to land an AI job](https://github.com/landedjobs/projects-to-land-an-ai-job)
- CData, [MCP server best practices 2026](https://www.cdata.com/blog/mcp-server-best-practices-2026)
- KDnuggets, [5 free ways to host a Python application](https://www.kdnuggets.com/5-free-ways-to-host-a-python-application) (April 2026)
