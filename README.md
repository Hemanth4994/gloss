# Gloss

Ask your data, grounded in your definitions. An open-source MCP server and a LangGraph agent for a data warehouse, with a
measured evaluation of when the agent is right, wrong, or should refuse.

**Status: data, definitions, MCP server, evaluation set, agent and evaluation runner are built. The agent has been run on the 30 development questions with a real model (results below). The 30 test questions have not been run yet.** See [PLAN.md](PLAN.md).

## What exists

- `src/gloss/datagen.py`: synthetic online-store data with a fixed seed and planted traps (test accounts, cancelled and returned orders, refunds in their own table, UTC timestamps reported in IST) plus a known number of injected dirty rows.
- `src/gloss/pipeline.py`: loads the rows into DuckDB with documented quality rules and writes a data-quality report. A test checks that the report matches exactly what was injected.
- `src/gloss/dictionary.yaml`: rules, table descriptions and nine metric definitions. Each metric has reference SQL, used only by tests and gold answers, never shown to the agent.
- `src/gloss/sqlguard.py` and `warehouse.py`: static checks on model-written SQL, then a read-only connection with no file or network access, a row cap and a timeout.
- `src/gloss/retrieval.py`: BM25 over the dictionary chunks (pure Python). Embeddings only if the retrieval evaluation shows a need.
- `src/gloss/tools.py` and `mcp_server.py`: four read-only tools (`list_tables`, `describe_table`, `search_definitions`, `run_sql`) served over MCP with the official Python SDK (v2), over stdio or streamable HTTP. A refused or failed query returns a tool error the model can read.
- `eval/questions.yaml`, `src/gloss/evaluation.py`: 60 questions, 30 dev and 30 test, each split with 12 definition questions, 8 join questions, 5 raw-table controls and 5 questions the data cannot answer (the right behaviour is to refuse). Gold SQL is written with macros so each trap (`test` accounts, `tz` IST vs UTC, `status`, `refund`) can be switched off to give a plausible wrong query. Tests prove every listed trap changes the answer, and that nothing is listed that does not. `eval/gold_answers.json` is the stored answer key; `scripts/make_gold.py` rebuilds it.
- Scoring is execution accuracy: the result of the agent's final SQL is compared with the gold result (column names ignored, extra columns allowed, integers exact, other numbers within the stated decimals, row order only for top-N questions). A refusal is correct only on the unanswerable questions. A scorer test replays the gold SQL (100%), refuses everything, never refuses, and ignores every definition, and checks each lands where it should.
- `src/gloss/agent.py`: a LangGraph graph (retrieve, plan, execute, repair at most twice, then answer, refuse or fail) around the same tool functions the MCP server exposes. The model is injected as a plain function and returns structured output (`sql`, `definitions_used`, `assumptions`, `answerable`, `refusal_reason`). At most 3 model calls per question. The answer text is built from the query result and the cited definitions, not written by the model. Three configurations, `definitions` (all rules plus BM25 top 3), `tables` (the description of every table and column, no rules or metrics) and `none` (bare schema only), share one system prompt that contains no business rule, and a test checks that. Each run carries LangSmith tags and metadata.
- `src/gloss/throttle.py`: request pacing for the free-tier rate limit, waits out per-minute 429s, stops cleanly on a daily quota.
- `scripts/run_eval.py`: runs a split in one configuration, scores it, resumes after an interruption, records the model that answered and hashes of the prompt, questions and answer key. The test split refuses to run unless the tag `frozen-v1` exists and `src/`, `eval/`, `scripts/` and `tests/` match it exactly.
- 119 tests: `PYTHONPATH=src:tests python -m unittest discover -s tests` (the agent tests need `pip install -e ".[agent]"` and are skipped without it). They include a check of every metric's SQL against an independent pure-Python calculation, a check that each planted trap really changes the answer, calls through a real in-process MCP client, and the evaluation-set checks above.

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

## Development runs (first runs, before any change)

Model: `gemini-3.5-flash-lite` on the free tier (the id the API reported for every call). One run per configuration on the 30 development questions, one model call per question, nothing tuned between the runs. Raw records: `results/dev_definitions.jsonl` and `results/dev_none.jsonl`, with `.summary.json` next to each.

| 30 dev questions | `definitions` | `none` |
|---|---|---|
| Definition questions | 12 / 12 | 0 / 12 |
| Join questions | 6 / 8 | 0 / 8 |
| Raw-table controls | 5 / 5 | 5 / 5 |
| Unanswerable (must refuse) | 5 / 5 | 5 / 5 |
| **Overall** | **28 / 30** | **10 / 30** |
| Wrong answers / refusals of answerable questions | 2 / 0 | 17 / 3 |
| Tokens in / out (30 calls) | 27,104 / 5,374 | 9,699 / 3,276 |

How to read this. The questions were written to need the definitions (IST dates, test accounts, order status, "last month"), so the gap shows that the agent uses supplied definitions correctly. It does not show how accurate the agent would be on a warehouse whose conventions it has to discover. `none` gives the model only table, column and type names, with no descriptions and no sample values, so it is a floor, not a competitor; the three refusals there are the model saying it cannot know what "active customer", "return rate" or the status values mean. That is why a third configuration, `tables` (full table and column descriptions, no rules or metrics), was added after seeing these results and before the test split was run. Both unanswerable-question scores are 5 / 5 in both configurations, so definitions neither helped nor hurt refusal on these five.

The two failures with definitions, both real agent errors (the gold answers are checked against an independent Python calculation):

| id | what the agent did | agent answer vs gold | category |
|---|---|---|---|
| j01 | Joined `order_items` straight to `orders` and subtracted the order-level discount once per item row | Delhi 1,100,817 vs Bengaluru 1,267,593 | wrong join grain (fan-out) |
| j08 | Applied "two or more orders" to each city group instead of to each customer, so it counted customers with a single completed order | 193 / 177 / 175 vs 119 / 117 / 114 | wrong aggregation level |

Both come from the same weakness: not aggregating to the right grain before combining tables. The system prompt was left unchanged, because adding a hint for these two cases would make the development score meaningless as an estimate.

Caveats. Each number is one sample: this model ignores the `temperature` setting, so a repeat run can differ. The questions and the dictionary have the same author. Mean time per question was about 4.4 s, which includes the request spacing the throttle adds to stay under the free-tier rate limit, so it is not a model-speed measurement.

## Sources used when planning

- Dataquest, [Best AI Projects to Build in 2026](https://www.dataquest.io/blog/ai-projects/) (July 2026)
- Landed, [projects to land an AI job](https://github.com/landedjobs/projects-to-land-an-ai-job)
- CData, [MCP server best practices 2026](https://www.cdata.com/blog/mcp-server-best-practices-2026)
- KDnuggets, [5 free ways to host a Python application](https://www.kdnuggets.com/5-free-ways-to-host-a-python-application) (April 2026)
