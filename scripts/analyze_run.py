"""Read an evaluation run and show what went wrong, or compare two runs question by question.

    python scripts/analyze_run.py failures results/dev_definitions.jsonl
    python scripts/analyze_run.py failures results/dev_definitions.jsonl --markdown results/failures_dev_definitions.md
    python scripts/analyze_run.py compare results/dev_definitions.jsonl results/dev_none.jsonl

`failures` prints, for every question that was not answered correctly, the question, what the agent did (its SQL, its rows or its
refusal, the definitions it was shown and said it used) next to the gold answer. With --markdown it also writes a table with an
empty `category` column, to fill in by hand after reading each case. It never calls a model.
"""
import argparse
import json
import os
import sys

from gloss.evaluation import GOOD, load_questions, run_gold
from gloss.tools import Warehouse

CATEGORIES = ("wrong definition applied", "definition not applied", "wrong join or grain", "wrong period", "wrong filter",
              "wrong columns or format", "refused but answerable", "answered but unanswerable", "SQL error", "gold or question problem")


def read_run(path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if "id" in rec:
                rows[rec["id"]] = rec
    return rows


def short(value, width: int = 220) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[:width - 3] + "..."


def failures(run: dict[str, dict], db: str) -> list[dict]:
    wh = Warehouse.open(db)
    questions = {q.id: q for q in load_questions()}
    out = []
    for qid, rec in run.items():
        if rec["outcome"] in GOOD:
            continue
        q = questions[qid]
        gold = [list(r) for r in run_gold(wh.con, wh.tables, q).rows[:5]] if q.answerable else None
        out.append({"id": qid, "kind": q.kind, "question": q.question, "outcome": rec["outcome"], "sql": rec["sql"],
                    "error": rec["error"], "agent_rows": (rec["rows"] or [])[:5] if rec["rows"] is not None else None,
                    "refusal_reason": rec["refusal_reason"], "gold_rows": gold, "needs": list(q.needs),
                    "retrieved": rec["retrieved"], "definitions_used": rec["definitions_used"], "assumptions": rec["assumptions"],
                    "why_unanswerable": q.why})
    return out


def print_failures(items: list[dict]) -> None:
    if not items:
        print("no failures")
    for it in items:
        print(f"\n=== {it['id']} ({it['kind']}) {it['outcome']}\nQ: {it['question']}")
        if it["why_unanswerable"]:
            print(f"data gap: {it['why_unanswerable']}")
        print(f"needs: {it['needs']}\nshown: {it['retrieved']}\nagent says it used: {it['definitions_used']}")
        if it["refusal_reason"]:
            print(f"agent refused: {short(it['refusal_reason'])}")
        if it["sql"]:
            print(f"agent SQL: {short(it['sql'], 600)}")
        if it["error"]:
            print(f"error: {short(it['error'])}")
        print(f"agent rows: {it['agent_rows']}\ngold rows : {it['gold_rows']}")
        if it["assumptions"]:
            print(f"assumptions: {short(it['assumptions'])}")


def write_markdown(items: list[dict], path: str) -> None:
    lines = ["| id | kind | outcome | what went wrong (fill in) | category (fill in) |", "|---|---|---|---|---|"]
    lines += [f"| {it['id']} | {it['kind']} | {it['outcome']} |  |  |" for it in items]
    lines += ["", "Categories: " + "; ".join(CATEGORIES)]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def compare(a: dict[str, dict], b: dict[str, dict], name_a: str, name_b: str) -> list[tuple]:
    ids = [i for i in a if i in b]
    return [(i, a[i]["kind"], a[i]["outcome"], b[i]["outcome"]) for i in ids]


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("failures")
    f.add_argument("run")
    f.add_argument("--db", default="data/gloss.duckdb")
    f.add_argument("--markdown", default=None)
    c = sub.add_parser("compare")
    c.add_argument("run_a")
    c.add_argument("run_b")
    args = ap.parse_args()

    if args.cmd == "failures":
        items = failures(read_run(args.run), args.db)
        print_failures(items)
        if args.markdown:
            write_markdown(items, args.markdown)
            print(f"\nwrote {args.markdown}")
    else:
        name_a, name_b = (os.path.basename(p).replace(".jsonl", "") for p in (args.run_a, args.run_b))
        rows = compare(read_run(args.run_a), read_run(args.run_b), name_a, name_b)
        print(f"{'id':<5}{'kind':<14}{name_a:<24}{name_b:<24}")
        for i, kind, oa, ob in rows:
            print(f"{i:<5}{kind:<14}{oa:<24}{ob:<24}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
