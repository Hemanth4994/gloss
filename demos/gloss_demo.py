"""Gloss demo: real code, real data, real stored model runs. No model call is made by this script.

From the root of the gloss folder (after `pip install -e ".[agent]"` and building data/gloss.duckdb):
    python demos/gloss_demo.py                      # or: python demos/gloss_demo.py path/to/gloss.duckdb
"""
import asyncio
import json
import sys
from pathlib import Path

import duckdb
from mcp.client import Client

from gloss.evaluation import load_questions
from gloss.mcp_server import build_server
from gloss.tools import Warehouse

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
DB = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "gloss.duckdb")
wh = Warehouse.open(DB)
server = build_server(wh)


def show(title):
    print("\n" + "=" * 8, title, "=" * 8)


def text(result):
    if getattr(result, "structured_content", None):
        return json.dumps(result.structured_content, default=str)[:700]
    return " ".join(getattr(c, "text", str(c)) for c in result.content)[:700]


async def mcp_part():
    async with Client(server) as c:
        show("1. The MCP server, called through a real MCP client")
        print("tools:", [t.name for t in (await c.list_tools()).tools])
        print("\n> search_definitions('net revenue', k=1)")
        print(text(await c.call_tool("search_definitions", {"query": "net revenue", "k": 1})))
        print("\n> run_sql('SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC')")
        print(text(await c.call_tool("run_sql", {"sql": "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC"})))
        for bad in ("DROP TABLE orders", "SELECT * FROM orders; DELETE FROM orders", "SELECT * FROM read_csv('/etc/passwd')"):
            print(f"\n> run_sql({bad!r})")
            try:
                print(text(await c.call_tool("run_sql", {"sql": bad})))
            except Exception as e:                                           # a refused query comes back as a tool error
                print("REFUSED:", str(e)[:200])


asyncio.run(mcp_part())

show("2. Why definitions matter: one question, six answers (net revenue, one month)")
con = wh.con                                                               # the same read-only connection the tools use
lo, hi = con.execute("select min(ordered_at), max(ordered_at) from orders").fetchone()
month = "2026-08"                                                           # a full month inside the data
start, end = f"{month}-01", "2026-09-01"
base = """WITH it AS (SELECT order_id, SUM(quantity * unit_price) AS items_total FROM order_items GROUP BY 1),
rf AS (SELECT order_id, SUM(refund_amount) AS refunded FROM refunds GROUP BY 1)
SELECT {expr} FROM orders o JOIN customers c ON c.customer_id = o.customer_id JOIN it ON it.order_id = o.order_id
LEFT JOIN rf ON rf.order_id = o.order_id WHERE {when} {extra}"""
utc = f"o.ordered_at >= TIMESTAMP '{start}' AND o.ordered_at < TIMESTAMP '{end}'"
ist = f"(o.ordered_at + INTERVAL 330 MINUTE) >= TIMESTAMP '{start}' AND (o.ordered_at + INTERVAL 330 MINUTE) < TIMESTAMP '{end}'"
steps = [
    ("no rules: every order, UTC month",                         "it.items_total", utc, ""),
    ("+ drop test accounts",                                     "it.items_total", utc, "AND NOT c.is_test"),
    ("+ only completed orders",                                  "it.items_total", utc, "AND NOT c.is_test AND o.status = 'completed'"),
    ("+ subtract the order's discount",                          "it.items_total - o.discount_amount", utc, "AND NOT c.is_test AND o.status = 'completed'"),
    ("+ subtract refunds",                                       "it.items_total - o.discount_amount - COALESCE(rf.refunded, 0)", utc, "AND NOT c.is_test AND o.status = 'completed'"),
    ("+ use the IST month (all rules applied)",                  "it.items_total - o.discount_amount - COALESCE(rf.refunded, 0)", ist, "AND NOT c.is_test AND o.status = 'completed'"),
]
final = None
for label, expr, when, extra in steps:
    final = con.execute(base.format(expr=f"SUM({expr})", when=when, extra=extra)).fetchone()[0]
    print(f"{label:<46} {final:>14,.0f}")
print(f"\nThe agent given no definitions gets the first number; with the company's rules it should reach the last: {final:,.0f}")

show("3. What the real model did on the frozen test set (stored results, not re-run)")
qs = {q.id: q for q in load_questions()}
rows = {}
for line in open(ROOT / "results" / "test_definitions.jsonl", encoding="utf-8"):
    r = json.loads(line)
    if "id" in r:
        rows[r["id"]] = r


def story(qid, note):
    r, q = rows[qid], qs[qid]
    print(f"\n--- {qid} ({q.kind}) outcome: {r['outcome']}  -- {note}")
    print("Q:", q.question)
    print("definitions shown to the model:", [x for x in r["retrieved"] if not x.startswith("rule:")], "+ all rules")
    print("definitions the model said it used:", r["definitions_used"])
    if r["refusal_reason"]:
        print("model refused:", r["refusal_reason"][:240])
    if r["sql"]:
        print("model SQL:\n" + "\n".join("   " + ln for ln in r["sql"].strip().splitlines()[:14]))
    if r["rows"] is not None:
        print("rows:", (r["rows"] or [])[:4])
    if r["assumptions"]:
        print("assumptions:", r["assumptions"])


good = next(i for i, r in rows.items() if qs[i].kind == "definition" and r["outcome"] == "correct" and r["sql"] and len(r["sql"]) < 700)
story(good, "a definition question the model got right")
story("t01", "one of the 3 misses")
unans = next(i for i, r in rows.items() if qs[i].kind == "unanswerable")
story(unans, "the data cannot answer this; refusing is the right answer")
