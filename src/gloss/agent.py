"""The Gloss agent: a small LangGraph graph around the same functions the MCP server exposes.

    retrieve -> plan -> execute -> finish
                  |        |
                  |        +--(error, budget left)--> plan   (repair, at most MAX_REPAIRS times)
                  +--(model says the data cannot answer it)--> refused

Three configurations differ ONLY in what retrieve puts in front of the model:
    definitions   every rule chunk plus the BM25 top 3 chunks for the question (the setting chosen on the dev split)
    tables        the description of every table and column, but no rule and no metric (what plain data documentation gives you)
    none          nothing but the bare schema (table names, column names and types)
The system prompt below is identical in all three and says nothing about the business rules, so the comparison is fair.

The model is called at most 1 + MAX_REPAIRS times per question. The model is injected as a plain function, so the whole graph
is tested with a scripted fake model and no API key.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .dictionary import chunks
from .evaluation import Prediction
from .tools import Warehouse, run_sql, search_definitions
from .warehouse import GuardError, QueryError, QueryTimeout

CONFIGS = ("definitions", "tables", "none")
TOP_K = 3                      # frozen on the dev split, see scripts/eval_retrieval.py
MAX_REPAIRS = 2
DEFAULT_MODEL = "gemini-3.5-flash-lite"

SYSTEM_PROMPT = """You answer questions about a company's data by writing one DuckDB SQL query.

Rules for the query:
- It must be a single SELECT statement that uses only the tables and columns listed below.
- Return only what the question asks for, with short readable column names. Do not add a LIMIT unless the question asks for a top N.
- Timestamps are stored as TIMESTAMP values. Amounts are integers.
- If any definitions are provided below the schema, follow them exactly. In definitions_used, list the id of every definition you relied on.
- If the tables cannot answer the question because the information is not stored anywhere, set answerable to false, leave sql empty,
  and say in refusal_reason what is missing. Do not answer a different question and do not guess a substitute.
- In assumptions, list any choice you had to make that the question or the definitions did not settle."""


class Plan(BaseModel):
    answerable: bool = Field(description="False only if the data cannot answer the question at all")
    sql: str = Field(description="One DuckDB SELECT query, or an empty string when answerable is false")
    definitions_used: list[str] = Field(description="Ids of the provided definitions that the query relies on, for example metric:net_revenue")
    assumptions: list[str] = Field(description="Choices made that the question or definitions did not settle")
    refusal_reason: str = Field(description="When answerable is false: what information is missing. Otherwise an empty string")


@dataclass
class PlanResult:
    plan: Plan | None
    error: str = ""                  # why there is no plan (the model returned something unparseable)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""                  # the model that actually answered, as reported by the API


Planner = Callable[[list[tuple[str, str]]], PlanResult]


class State(TypedDict, total=False):
    question: str
    config: str
    retrieved: list[str]
    context: str
    attempts: int
    input_tokens: int
    output_tokens: int
    models: list[str]
    plan: dict | None
    sql: str
    last_error: str
    result: dict | None
    status: str                      # answered | refused | failed
    refusal_reason: str
    answer: str
    steps: list[dict]


def schema_text(wh: Warehouse) -> str:
    """Bare schema: table names, column names and types. No descriptions, so no definitions leak into the 'none' configuration."""
    lines = []
    for t in wh.tables:
        cols = wh.con.execute("SELECT column_name, data_type FROM information_schema.columns "
                              "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position", [t]).fetchall()
        lines.append(f"{t}(" + ", ".join(f"{c} {ty}" for c, ty in cols) + ")")
    return "\n".join(lines)


def build_messages(schema: str, context: str, question: str, previous_sql: str = "", error: str = "") -> list[tuple[str, str]]:
    parts = [f"Tables and columns:\n{schema}"]
    if context:
        parts.append(f"Definitions:\n{context}")
    parts.append(f"Question: {question}")
    if error:
        parts.append(f"Your previous query did not work.\nQuery: {previous_sql}\nError: {error}\nWrite a corrected query.")
    return [("system", SYSTEM_PROMPT), ("human", "\n\n".join(parts))]


def render_answer(state: State) -> str:
    """The answer shown to a person, built without a model so it can only contain what the query returned."""
    titles = {c.id: c.title for c in chunks()}
    res = state["result"]
    cols, rows = res["columns"], res["rows"]
    lines = []
    if len(rows) == 1 and len(cols) == 1:
        lines.append(f"{cols[0]}: {rows[0][0]}")
    else:
        lines.append(" | ".join(cols))
        lines += [" | ".join(str(v) for v in r) for r in rows[:15]]
        if len(rows) > 15 or res.get("truncated"):
            lines.append(f"... showing 15 of {len(rows)}{'+' if res.get('truncated') else ''} rows")
    plan = state.get("plan") or {}
    used = plan.get("definitions_used") or []
    if used:
        lines.append("Definitions used: " + "; ".join(f"{titles.get(i, 'unknown id')} ({i})" for i in used))
    if plan.get("assumptions"):
        lines.append("Assumptions: " + "; ".join(plan["assumptions"]))
    lines.append(f"SQL: {state['sql']}")
    return "\n".join(lines)


def build_agent(wh: Warehouse, planner: Planner, max_repairs: int = MAX_REPAIRS, top_k: int = TOP_K):
    all_chunks = chunks()
    text_of = {c.id: c.text for c in all_chunks}
    rule_ids = [c.id for c in all_chunks if c.kind == "rule"]
    table_ids = [c.id for c in all_chunks if c.kind == "table"]
    schema = schema_text(wh)

    def retrieve(state: State) -> State:
        if state["config"] not in CONFIGS:
            raise ValueError(f"config must be one of {CONFIGS}")
        ids: list[str] = []
        if state["config"] == "definitions":
            ids = rule_ids + [h["id"] for h in search_definitions(wh, state["question"], top_k) if h["id"] not in rule_ids]
        elif state["config"] == "tables":
            ids = table_ids
        return {"retrieved": ids, "context": "\n".join(f"[{i}] {text_of[i]}" for i in ids), "attempts": 0,
                "input_tokens": 0, "output_tokens": 0, "models": [], "last_error": "", "sql": "", "result": None,
                "plan": None, "status": "", "refusal_reason": "", "answer": "", "steps": [{"node": "retrieve", "ids": ids}]}

    def plan(state: State) -> State:
        msgs = build_messages(schema, state["context"], state["question"], state["sql"], state["last_error"])
        res = planner(msgs)
        step = {"node": "plan", "attempt": state["attempts"] + 1, "model": res.model}
        out: State = {"attempts": state["attempts"] + 1, "input_tokens": state["input_tokens"] + res.input_tokens,
                      "output_tokens": state["output_tokens"] + res.output_tokens,
                      "models": state["models"] + ([res.model] if res.model else [])}
        p = res.plan
        if p is None:
            out.update(last_error=f"the reply was not a valid answer ({res.error or 'no structured output'})", sql="")
            step["error"] = out["last_error"]
        elif not p.answerable:
            out.update(status="refused", refusal_reason=p.refusal_reason, plan=p.model_dump(), sql="", last_error="")
            step["refused"] = p.refusal_reason
        elif not p.sql.strip():
            out.update(last_error="answerable was true but sql was empty", sql="", plan=p.model_dump())
            step["error"] = out["last_error"]
        else:
            out.update(sql=p.sql.strip(), plan=p.model_dump(), last_error="")
            step["sql"] = out["sql"]
        out["steps"] = state["steps"] + [step]
        return out

    def execute(state: State) -> State:
        step = {"node": "execute", "sql": state["sql"]}
        try:
            result = run_sql(wh, state["sql"])
            out: State = {"result": result, "last_error": ""}
            step["rows"] = len(result["rows"])
        except (GuardError, QueryError, QueryTimeout) as exc:
            out = {"result": None, "last_error": f"{type(exc).__name__}: {exc}"}
            step["error"] = out["last_error"]
        out["steps"] = state["steps"] + [step]
        return out

    def finish(state: State) -> State:
        done = {**state, "status": "answered"}
        return {"status": "answered", "answer": render_answer(done)}

    def fail(state: State) -> State:
        return {"status": "failed", "answer": f"No answer: {state['last_error']}"}

    def after_plan(state: State) -> str:
        if state["status"] == "refused":
            return "refused"
        if state["last_error"]:
            return "retry" if state["attempts"] <= max_repairs else "fail"
        return "execute"

    def after_execute(state: State) -> str:
        if not state["last_error"]:
            return "finish"
        return "retry" if state["attempts"] <= max_repairs else "fail"

    g = StateGraph(State)
    for name, fn in (("retrieve", retrieve), ("plan", plan), ("execute", execute), ("finish", finish), ("fail", fail)):
        g.add_node(name, fn)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "plan")
    g.add_conditional_edges("plan", after_plan, {"refused": END, "retry": "plan", "fail": "fail", "execute": "execute"})
    g.add_conditional_edges("execute", after_execute, {"finish": "finish", "retry": "plan", "fail": "fail"})
    g.add_edge("finish", END)
    g.add_edge("fail", END)
    return g.compile()


def run_question(agent, question: str, config: str = "definitions", qid: str = "", split: str = "", model: str = "") -> dict:
    """Run one question and return the final state plus the wall-clock time. Metadata makes the LangSmith trace searchable."""
    start = time.perf_counter()
    final = agent.invoke({"question": question, "config": config},
                         config={"run_name": f"gloss:{qid or 'adhoc'}", "tags": ["gloss", config] + ([split] if split else []),
                                 "metadata": {"question_id": qid, "config": config, "split": split, "model": model}})
    final["latency_s"] = round(time.perf_counter() - start, 3)
    final["model_calls"] = final["attempts"]
    return final


def to_prediction(final: dict) -> Prediction:
    """What the scorer needs: a refusal, or the rows of the final successful query. A failed run has neither."""
    if final["status"] == "refused":
        return Prediction(refused=True)
    if final["status"] == "answered" and final.get("result"):
        return Prediction(rows=[tuple(r) for r in final["result"]["rows"]])
    return Prediction()


def gemini_planner(model: str | None = None, throttle=None) -> Planner:
    """The real model. Needs GOOGLE_API_KEY. The returned function carries .model, the id that was requested."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    model = model or os.environ.get("GLOSS_MODEL", DEFAULT_MODEL)
    # No temperature argument: gemini-3.5-flash-lite ignores it ("fixed sampling defaults"), so passing 0 only printed a warning on
    # every call and suggested the runs were deterministic. They are not, so a single run is one sample, not an exact result.
    llm = ChatGoogleGenerativeAI(model=model).with_structured_output(Plan, include_raw=True)

    def planner(messages: list[tuple[str, str]]) -> PlanResult:
        out = throttle.call(lambda: llm.invoke(messages)) if throttle else llm.invoke(messages)
        raw, parsed, err = out.get("raw"), out.get("parsed"), out.get("parsing_error")
        usage = getattr(raw, "usage_metadata", None) or {}
        meta = getattr(raw, "response_metadata", None) or {}
        return PlanResult(plan=parsed, error=str(err) if err else ("" if parsed else "no structured output"),
                          input_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)),
                          model=str(meta.get("model_version") or meta.get("model_name") or model))

    planner.model = model        # type: ignore[attr-defined]
    return planner


def main() -> None:
    import argparse
    from .throttle import Throttle

    ap = argparse.ArgumentParser(description="Ask the Gloss agent one question (needs GOOGLE_API_KEY).")
    ap.add_argument("question")
    ap.add_argument("--db", default="data/gloss.duckdb")
    ap.add_argument("--config", choices=CONFIGS, default="definitions")
    args = ap.parse_args()
    planner = gemini_planner(throttle=Throttle())
    final = run_question(build_agent(Warehouse.open(args.db), planner), args.question, args.config, model=planner.model)
    print(final["answer"] if final["status"] != "refused" else f"Cannot answer from this data: {final['refusal_reason']}")
    print(f"\nstatus={final['status']} model_calls={final['model_calls']} tokens={final['input_tokens']}+{final['output_tokens']} "
          f"latency={final['latency_s']}s model={','.join(sorted(set(final['models']))) or planner.model}")


if __name__ == "__main__":
    main()
