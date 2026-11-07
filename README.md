# Gloss

Ask your data, grounded in your definitions. An open-source MCP server and a LangGraph agent for a data warehouse, with a
measured evaluation of when the agent is right, wrong, or should refuse.

**Status: data, definitions, MCP server, evaluation set, agent and evaluation runner are built, and the frozen test split has been run once in each of three configurations (results below). Not done yet: the public deployment.** See [PLAN.md](PLAN.md).

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
- 135 tests: `PYTHONPATH=src:tests python -m pytest analysis tests` (the agent and demo tests need `pip install -e ".[agent]"` and are skipped without it). They include a check of every metric's SQL against an independent pure-Python calculation, a check that each planted trap really changes the answer, calls through a real in-process MCP client, and the evaluation-set checks above.

- `analysis/` and `space/` were written after the freeze, so they sit outside the frozen folders: the failure analysis of the runs, and the demo app (a Gradio page plus the MCP endpoint at `/mcp` in one container, with a per-client rate limit on the MCP path, a read-only synthetic database, and a small daily budget for the shared model key). `space/Dockerfile` installs the code from the tag `frozen-v1`, so the demo runs exactly the code that was evaluated.

Build the warehouse, then serve it:

```
PYTHONPATH=src python -m gloss.pipeline --out data/gloss.duckdb
PYTHONPATH=src python -m gloss.mcp_server --db data/gloss.duckdb              # stdio (Claude Desktop, MCP Inspector)
PYTHONPATH=src python -m gloss.mcp_server --db data/gloss.duckdb --http       # streamable HTTP at http://localhost:8000/mcp
```

## Run the demo

`demos/gloss_demo.py` shows the real code and data end to end. It makes no model call, so it needs no API key.

```
pip install -e .
python demos/gloss_demo.py
```

It calls the MCP server through a real MCP client (including three queries the guard refuses), shows one question
answered six ways as the company's rules are applied one by one, and replays three stored runs from the frozen test set.

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

## Test results (frozen)

The 30 test questions were run once per configuration on the code tagged `frozen-v1` (commit `ccd36bf`: prompt, retrieval, questions and gold answers fixed before any test run). Model: `gemini-3.5-flash-lite` on the free tier, the id the API reported for every call. Raw records and summaries: `results/test_<config>.jsonl` and `.summary.json`. Intervals are 95% Wilson intervals from `results/failure_analysis.json`.

| 30 test questions | `definitions` | `tables` | `none` |
|---|---|---|---|
| Definition questions (12) | 10 | 1 | 0 |
| Join questions (8) | 7 | 0 | 0 |
| Raw-table controls (5) | 5 | 5 | 5 |
| Unanswerable, must refuse (5) | 5 | 5 | 5 |
| **Overall** | **27 / 30 (90.0%)** | **11 / 30 (36.7%)** | **10 / 30 (33.3%)** |
| 95% interval | 74.4% to 96.5% | 21.9% to 54.5% | 19.2% to 51.2% |
| Wrong answers / refusals of answerable questions | 3 / 0 | 16 / 3 | 20 / 0 |
| Model calls (30 questions) | 30 | 30 | 32 |
| Tokens in / out | 27,420 / 5,566 | 20,737 / 3,749 | 10,876 / 4,677 |

What the configurations show the model:
- `definitions`: every rule plus the BM25 top 3 chunks for the question (rules, metric definitions, table descriptions).
- `tables`: the description of every table and column, no rules and no metrics.
- `none`: only table names, column names and types.

What this says.
1. The definitions are what make the difference. With them the agent got 27 of 30; without them 10 or 11, and the intervals do not overlap. The same ordering held on the development split.
2. Documentation of tables and columns alone did not help (11 vs 10 on test, 10 vs 10 on dev). The model still did not know what "net revenue", "new customer" or "last month" mean here. It became more cautious instead: `tables` refused 3 answerable questions on test and 7 on dev, saying the term was not defined, where `none` mostly guessed.
3. Refusing what the data cannot answer worked in every configuration (5 of 5 each time), so definitions neither helped nor hurt refusal on these five questions. That says little about harder or subtler cases.
4. Controls (raw table questions where no rule should be applied) were 5 of 5 everywhere, so supplying rules did not make the agent apply them where they do not belong.

## Failure analysis

Every wrong answer on the test split is tagged, and so are the wrong answers of both development runs with definitions. The table is `results/failure_analysis.md`, built by `analysis/classify_failures.py`. For each wrong answer it first checks mechanically whether the agent's result equals the gold query with some of the question's traps (test accounts, IST dates, order status, refunds) switched off. Everything else was tagged by hand after reading the SQL (`analysis/manual_tags.yaml`).

With definitions, all 3 test failures and all 4 development failures (two runs) are the same kind of error: not combining tables at the right grain. Four repeat an order-level amount once per item row after joining `order_items` (for example the discount subtracted for every item), and three apply a condition or a MAX at the wrong level (per city instead of per customer, MAX instead of SUM across a customer's orders). In every one of the seven the agent cited the right definitions and applied the test-account, IST and status rules correctly; what failed was the SQL. The prompt was deliberately not changed in response, because that would turn the development split into training data.

| Wrong answers on the test split | `definitions` | `tables` | `none` |
|---|---|---|---|
| Equal to the gold query with test accounts / IST dates / status ignored | 0 | 8 | 8 |
| Relative period from the system clock, not the as-of date | 0 | 4 | 3 |
| "New customer" taken from signup, not first purchase | 0 | 2 | 2 |
| Metric defined differently (statuses, discounts, refunds, GMV after discount, fractional days) | 0 | 1 | 4 |
| Order-level amount repeated per item row (fan-out) | 2 | 1 | 2 |
| Condition or MAX at the wrong level | 1 | 0 | 0 |
| Returns approximated with the refunds table | 0 | 0 | 1 |
| **Total wrong answers** | **3** | **16** | **20** |

The clock failures are worth a sentence: asked about "the last 30 days", the baselines used `NOW()`, which is after the end of the data, and sometimes returned 0 or NULL. A warehouse with a fixed as-of date needs that written down, which is what the `relative_periods` rule does.

Development split, for reference (each one run, `results/dev_*.jsonl`): `definitions` 28 / 30 twice, but not the same questions: j01 failed both times, j08 only in the first run and d10 only in the second. `tables` 10 / 30, `none` 10 / 30. The dev wrong answers of `none` and `tables` were not tagged by hand (14 of them are unexplained in `results/failure_analysis.md`).

## Limits of this evidence

- 30 test questions per configuration. A score of 27 / 30 is compatible with a true rate anywhere from about 74% to 97%, and one question can flip between runs: this model ignores the `temperature` setting, so each number is one sample, not a fixed result.
- I wrote the questions, the gold SQL and the dictionary. The agent is scored on using supplied definitions correctly, not on discovering a company's conventions, and the questions were written to depend on them. The wide gap between configurations is partly by design.
- The unanswerable and control sets have 5 questions each per split, too few to say much beyond "no failures seen".
- The `tables` configuration was added after seeing the development results (because `none` is only a bare schema), and before any test run. No prompt, retrieval or question was changed after the development runs.
- The synthetic data is small (2,500 customers, about 5,700 orders). Nothing here says how the agent behaves on a real, messy warehouse.
- Run headers record SHA-256 prefixes of `eval/questions.yaml` and `eval/gold_answers.json`; they were computed on a Windows checkout with CRLF line endings, so they equal the hashes of the committed files with CRLF and differ from a hash taken on Linux. The files themselves are the tagged ones.
- After the test runs, one change was made under the frozen folders: `tests/test_run_eval.py` now points the freeze check at a tag name that does not exist, because the real `frozen-v1` tag made that test fail once the tag existed. It touches no result and no code that produced one; `git diff frozen-v1 HEAD -- src eval scripts` is empty.
- Time per question is not reported: the request spacing the throttle adds to stay under the free-tier rate limit dominates it.

## Development runs (before the freeze)

Same model, one run each on the 30 development questions, nothing tuned between them: `definitions` 28 / 30 (run 1: definition 12/12, join 6/8; run 2: definition 11/12, join 7/8; control and unanswerable 5/5 and 5/5 both times), `none` 10 / 30 (wrong 17, refused 3), `tables` 10 / 30 (wrong 13, refused 7). Raw records: `results/dev_*.jsonl`.

## Sources used when planning

- Dataquest, [Best AI Projects to Build in 2026](https://www.dataquest.io/blog/ai-projects/) (July 2026)
- Landed, [projects to land an AI job](https://github.com/landedjobs/projects-to-land-an-ai-job)
- CData, [MCP server best practices 2026](https://www.cdata.com/blog/mcp-server-best-practices-2026)
- KDnuggets, [5 free ways to host a Python application](https://www.kdnuggets.com/5-free-ways-to-host-a-python-application) (April 2026)
