"""Gloss on Hugging Face Spaces: one container serving a web page and the MCP endpoint.

    /      a Gradio page: try the tools, ask the agent, read the evaluation
    /mcp   the Gloss MCP server over streamable HTTP (stateless), for any MCP client

The database is synthetic and read-only. The tools need no model key. The agent tab uses a Gemini key the visitor pastes (used for that
one request, never stored or logged) or, if the owner set GOOGLE_API_KEY as a Space secret, a small daily budget (GLOSS_DAILY_CAP).
Run locally:  GLOSS_DB=data/gloss.duckdb python space/app.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import threading
import time
from collections import defaultdict, deque

import gradio as gr
import uvicorn
from fastapi import FastAPI
from langchain_google_genai import ChatGoogleGenerativeAI

from gloss import tools
from gloss.agent import CONFIGS, DEFAULT_MODEL, Plan, PlanResult, build_agent, run_question
from gloss.mcp_server import build_server
from gloss.throttle import DailyQuotaExceeded, Throttle
from gloss.warehouse import GuardError, QueryError, QueryTimeout

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("GLOSS_DB", os.path.join(HERE, "data", "gloss.duckdb"))
DAILY_CAP = int(os.environ.get("GLOSS_DAILY_CAP", "25"))          # agent questions per day paid for by the owner's key
MCP_REQUESTS_PER_MINUTE = int(os.environ.get("GLOSS_MCP_RPM", "60"))
GITHUB = "https://github.com/Hemanth4994/gloss"

wh = tools.Warehouse.open(DB)

EXAMPLES = [
    "What was net revenue in August 2026?",
    "How many active customers do we have today?",
    "How many new customers did we get last month?",
    "Which city has the highest net revenue?",
    "What is our profit margin by product category?",
]


# ----------------------------------------------------------------------------------------------------------------- tools tab
def as_table(columns, rows) -> dict:
    """A Gradio table value that keeps the real column names (a bare list of rows would be headed 1, 2, 3)."""
    return {"headers": list(columns) or ["result"], "data": [list(r) for r in rows]}


def search(query: str, k: int = 3) -> str:
    query = (query or "").strip()[:300]
    if not query:
        return "Type a question to see which definitions Gloss would retrieve."
    hits = tools.search_definitions(wh, query, int(k))
    if not hits:
        return "No definition matched."
    return "\n\n".join(f"**{h['id']}** (score {h['score']})\n\n{h['text']}" for h in hits)


def run_sql(sql: str):
    sql = (sql or "").strip()[:4000]
    if not sql:
        return as_table([], []), "Write one SELECT query."
    try:
        res = tools.run_sql(wh, sql)
    except (GuardError, QueryError, QueryTimeout) as exc:
        return as_table([], []), f"Refused or failed: {exc}"
    note = f"{len(res['rows'])} rows in {res['elapsed_s']} s" + (" (cut at the row cap)" if res["truncated"] else "")
    return as_table(res["columns"], res["rows"]), note


def schema_markdown() -> str:
    lines = []
    for t in tools.list_tables(wh):
        d = tools.describe_table(wh, t["table"])
        cols = ", ".join(f"`{c['name']}` {c['type']}" for c in d["columns"])
        lines.append(f"**{t['table']}**: {t['description']}  \n{cols}")
    return "\n\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------- agent tab
_budget = {"day": None, "used": 0}
_budget_lock = threading.Lock()


def take_owner_budget() -> bool:
    today = dt.datetime.now(dt.timezone.utc).date()
    with _budget_lock:
        if _budget["day"] != today:
            _budget["day"], _budget["used"] = today, 0
        if _budget["used"] >= DAILY_CAP:
            return False
        _budget["used"] += 1
        return True


def planner_for(api_key: str, throttle: Throttle, model: str = DEFAULT_MODEL):
    """Same as gloss.agent.gemini_planner, but with a key passed in instead of read from the environment."""
    llm = ChatGoogleGenerativeAI(model=model, api_key=api_key).with_structured_output(Plan, include_raw=True)

    def planner(messages):
        out = throttle.call(lambda: llm.invoke(messages))
        raw, parsed, err = out.get("raw"), out.get("parsed"), out.get("parsing_error")
        usage = getattr(raw, "usage_metadata", None) or {}
        meta = getattr(raw, "response_metadata", None) or {}
        return PlanResult(plan=parsed, error=str(err) if err else ("" if parsed else "no structured output"),
                          input_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)),
                          model=str(meta.get("model_version") or meta.get("model_name") or model))
    return planner


def ask(question: str, config: str, visitor_key: str):
    """Returns (answer text, sql, result table, details)."""
    question = (question or "").strip()[:400]
    visitor_key = (visitor_key or "").strip()
    if not question:
        return "Type a question first.", "", as_table([], []), {}
    if config not in CONFIGS:
        return "Unknown configuration.", "", as_table([], []), {}
    key = visitor_key
    if not key:
        key = os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            return "This demo has no model key. Paste your own free Gemini key above, or use the Tools tab, which needs none.", "", as_table([], []), {}
        if not take_owner_budget():
            return (f"Today's shared budget of {DAILY_CAP} agent questions is used up. Paste your own free Gemini key to continue, "
                    "or use the Tools tab."), "", as_table([], []), {}
    try:
        agent = build_agent(wh, planner_for(key, Throttle()))
        final = run_question(agent, question, config, model=DEFAULT_MODEL)
    except DailyQuotaExceeded:
        return "The model's daily quota for this key is used up. Try again tomorrow or use another key.", "", as_table([], []), {}
    except Exception as exc:                                              # a bad key, a network error, a model error
        msg = " ".join(str(exc).split())[:240].replace(key, "***")
        return f"The model call failed: {msg}", "", as_table([], []), {}
    details = {"status": final["status"], "configuration": config, "definitions shown": final["retrieved"],
               "definitions the model says it used": (final.get("plan") or {}).get("definitions_used", []),
               "assumptions": (final.get("plan") or {}).get("assumptions", []), "model calls": final["model_calls"],
               "tokens in/out": [final["input_tokens"], final["output_tokens"]], "seconds": final["latency_s"],
               "model": ", ".join(sorted(set(final["models"]))) or DEFAULT_MODEL}
    if final["status"] == "refused":
        return f"Cannot answer from this data: {final['refusal_reason']}", "", as_table([], []), details
    if final["status"] != "answered":
        return final["answer"], final["sql"], as_table([], []), details
    return final["answer"].split("\nSQL:")[0], final["sql"], as_table(final["result"]["columns"], final["result"]["rows"]), details


# ----------------------------------------------------------------------------------------------------------------- results tab
def results_markdown() -> str:
    path = os.path.join(HERE, "results_summary.json")
    if not os.path.exists(path):
        return "Results are in the repository: " + GITHUB
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    lines = ["Frozen test split: 30 questions, run once per configuration. All numbers come from the files in `results/` in the repository.", "",
             "| configuration | correct | 95% interval | definition | join | control | unanswerable |", "|---|---|---|---|---|---|---|"]
    for c in r["test"]:
        k = c["by_kind"]
        lines.append(f"| {c['config']} | {c['correct']} / {c['n']} | {c['wilson95'][0]:.0%} to {c['wilson95'][1]:.0%} | "
                     f"{k['definition']} | {k['join']} | {k['control']} | {k['unanswerable']} |")
    lines += ["", r["note"]]
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------- page
with gr.Blocks(title="Gloss") as demo:
    gr.Markdown(
        "# Gloss\nAn open-source MCP server and a LangGraph agent that answer data questions using a company's own metric "
        "definitions, with a measured evaluation. The warehouse is **synthetic** and read-only.\n\n"
        f"MCP endpoint for any MCP client: this page's address plus `/mcp`. Code, results and limits: [{GITHUB}]({GITHUB}).")
    with gr.Tab("Tools (no key needed)"):
        gr.Markdown("These are the same four tools the MCP server exposes.")
        with gr.Accordion("list_tables / describe_table", open=False):
            gr.Markdown(schema_markdown())
        with gr.Row():
            q = gr.Textbox(label="search_definitions: a question", value=EXAMPLES[0], scale=4)
            k = gr.Slider(1, 8, value=3, step=1, label="how many", scale=1)
        out = gr.Markdown()
        gr.Button("Search definitions").click(search, [q, k], out, api_name="search_definitions")
        sql = gr.Code(language="sql", label="run_sql: one SELECT (guarded, read-only, 200 rows, 10 s)",
                      value="SELECT status, COUNT(*) AS orders FROM orders GROUP BY status ORDER BY orders DESC")
        table, note = gr.Dataframe(label="result", interactive=False), gr.Markdown()
        gr.Button("Run").click(run_sql, sql, [table, note], api_name="run_sql")
    with gr.Tab("Ask the agent"):
        gr.Markdown("The agent retrieves definitions, writes one SQL query, runs it through the same guard, and answers with the SQL "
                    "and the definitions it used. Pick `none` to see the same model answering without any definitions.")
        question = gr.Textbox(label="Question", value=EXAMPLES[0], lines=2)
        with gr.Row():
            config = gr.Radio(list(CONFIGS), value="definitions", label="what the model is shown")
            key = gr.Textbox(label="Your Gemini API key (optional)", type="password",
                             info="Used for this request only, not stored or logged. Leave empty to use the shared daily budget.")
        gr.Examples(EXAMPLES, question)
        go = gr.Button("Ask")
        answer = gr.Markdown()
        sql_out = gr.Code(language="sql", label="SQL that ran")
        rows_out = gr.Dataframe(label="rows", interactive=False)
        details = gr.JSON(label="details")
        go.click(ask, [question, config, key], [answer, sql_out, rows_out, details], api_name="ask")
    with gr.Tab("Evaluation"):
        gr.Markdown(results_markdown())


# ----------------------------------------------------------------------------------------------------------------- web app
class RateLimit:
    """Pure ASGI limiter for the /mcp path: at most MCP_REQUESTS_PER_MINUTE requests per client address per minute."""

    def __init__(self, app, limit: int = MCP_REQUESTS_PER_MINUTE, window: float = 60.0, clock=time.monotonic):
        self.app, self.limit, self.window, self.clock = app, limit, window, clock
        self.seen: dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = dict(scope["headers"])
            who = headers.get(b"x-forwarded-for", b"").decode().split(",")[0].strip() or (scope.get("client") or ("?",))[0]
            now, hits = self.clock(), self.seen[who]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                body = json.dumps({"error": f"rate limit: {self.limit} requests per minute"}).encode()
                await send({"type": "http.response.start", "status": 429,
                            "headers": [(b"content-type", b"application/json"), (b"retry-after", b"30")]})
                await send({"type": "http.response.body", "body": body})
                return
            hits.append(now)
            if len(self.seen) > 5000:                                        # forget idle clients
                for addr in [a for a, d in self.seen.items() if not d or now - d[-1] > self.window]:
                    del self.seen[addr]
        await self.app(scope, receive, send)


def build_app() -> FastAPI:
    mcp_app = build_server(wh).streamable_http_app(host="0.0.0.0", stateless_http=True, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):                 # starts the MCP session manager
            yield

    api = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    api.router.routes.extend(mcp_app.routes)                                  # /mcp is served by the SDK's own handler
    api.add_middleware(RateLimit)
    return gr.mount_gradio_app(api, demo, path="/")


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
