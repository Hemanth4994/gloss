# Gloss: plan

**One line:** an open-source MCP server plus a LangGraph agent that answers questions about a data warehouse, grounded in the
company's own metric definitions, with a measured evaluation of when it is right, when it is wrong, and when it should refuse.

## Why this project

- Hiring signals found in current sources (see README sources): an evaluation set and failure analysis, a live link,
  cost numbers and a limitations section matter more than the model. Plain "chat with your database" projects are common,
  so the chat is not the point. The measured evaluation is.
- It uses everything on the wish list: **MCP** (the server is the product), **RAG** (retrieval over metric definitions),
  **LangGraph** (the agent), **LangSmith** (traces and an evaluation dataset), **LangChain** (model access, structured output).
- It points at data roles: SQL, a small data pipeline with quality checks, a data dictionary, metrics.
- It reuses the habits from Rung: freeze before testing, pin the model, report failures, state limits.

**Risk, stated up front.** If retrieving definitions does not improve accuracy, the honest write-up says so. The definitions are
written by me, so the test is "does the agent use supplied definitions correctly", not "can it guess a company's conventions".

## Parts

1. **Warehouse.** A small synthetic e-commerce warehouse in DuckDB (customers, orders, order_items, refunds; the payment method is a column on orders),
   generated with a fixed seed, with realistic traps planted on purpose: cancelled and returned orders, test accounts,
   timestamps stored in UTC but reported in IST, refunds in a separate table. A data-quality report logs rows the loader rejects.
2. **Data dictionary.** YAML: table and column descriptions plus metric definitions ("net revenue", "active customer",
   "repeat customer", ...). Each definition is one retrievable chunk.
3. **Safe SQL layer** (built): `sqlguard.py` (static checks) and `warehouse.py` (read-only connection, no file or network access,
   row cap, timeout).
4. **MCP server** (official Python SDK): tools `list_tables`, `describe_table`, `search_definitions`, `run_sql`. Works from
   any MCP client over stdio, and over streamable HTTP when deployed.
5. **Retrieval.** BM25 first. Add embeddings only if the retrieval evaluation shows BM25 missing definitions.
6. **Agent** (LangGraph): retrieve definitions, write SQL (structured output: sql, definitions used, assumptions, answerable),
   guard and execute, repair at most twice, answer with the SQL and the definitions cited, or refuse. Hard cap on model calls per question.
7. **Evaluation.** About 60 questions with gold SQL, split 30 development and 30 test, in four kinds: needs a definition,
   does not need one (control), needs a join, cannot be answered from the data (the right answer is a refusal).
   Metrics: execution accuracy (result sets match), refusal correctness, retrieval recall@3, model calls, tokens, latency, cost.
   Two configurations on the test set: with and without retrieved definitions. Failure taxonomy for every wrong answer.
8. **Deployment.** One container on Hugging Face Spaces (free CPU): a Gradio page and the MCP endpoint. The tools work without
   any model key. The agent tab uses a visitor-supplied key or a small daily cap, so the public demo cannot drain the owner's quota.

## Rules

- Prompts, retrieval settings and the test split are frozen (git tag) before the test run. The test set is run once per configuration.
- Pin the exact model id and record which model answered. No floating aliases.
- Every number in the README comes from a file in `results/`.
- Public endpoint: read-only tools, rate limiting, nothing secret in the repo. Auth for a real deployment is OAuth 2.1; the demo
  exposes only a synthetic database and says so.

## Schedule (target: finished by Fri 6 Nov, buffer to Mon 9 Nov)

| Day | Work |
|---|---|
| Fri 30 Oct | Repo, safe SQL layer with tests (done), plan |
| Sat 31 Oct | Data generator, warehouse build, quality report, data dictionary |
| Sun 1 Nov | MCP tools, BM25 retrieval, retrieval evaluation |
| Mon 2 Nov | LangGraph agent, LangSmith tracing, first runs on the development questions |
| Tue 3 Nov | Write all 60 questions with gold SQL, review them, tag `frozen-v1` |
| Wed 4 Nov | Test runs (two configurations), failure analysis |
| Thu 5 Nov | Deploy to Hugging Face Spaces, README results |
| Fri 6 Nov | Resume bullets, LinkedIn post, demo recording |

## Progress

- Done ahead of schedule (Sun 1 Nov): data generator and loader, data dictionary, MCP server over stdio and HTTP, BM25, the 60 questions
  with gold SQL, the scorer, and the retrieval check on the dev split.
- Retrieval choice, made on dev only: all rule chunks plus the BM25 top 3. Plain BM25 gave every needed chunk for 5% of dev questions.
- Agent (`agent.py`), request throttle and evaluation runner (`scripts/run_eval.py`) written and tested with a scripted fake model.
  Never run against a real model yet.
- Dev runs done (Mon 2 Nov): `definitions` 28 / 30, `none` 10 / 30, results in `results/`. Two failures, both wrong grain in a join; nothing changed in response.
- Added a third configuration, `tables` (table and column descriptions only), so the comparison has a fairer baseline than the bare schema. Added before the freeze.
- Froze at commit `ccd36bf` (tag `frozen-v1`) and ran the test split once per configuration (Mon 2 Nov): `definitions` 27 / 30, `tables` 11 / 30, `none` 10 / 30. Every wrong answer on the test split is tagged (`results/failure_analysis.md`).
- Next: deploy to Hugging Face Spaces (one container: Gradio page plus the MCP endpoint, in a folder outside the frozen `src/`, `eval/`, `scripts/`, `tests/`), then resume bullets, LinkedIn post and demo.

## Not doing

Fine-tuning, a vector database server, multi-database support, auth flows, a polished front end, a second agent framework.
