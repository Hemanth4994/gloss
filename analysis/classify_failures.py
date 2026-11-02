"""Post-hoc failure analysis of the evaluation runs in results/. Written after the freeze, so it lives outside src/, eval/, scripts/ and tests/.

    python analysis/classify_failures.py --db data/gloss.duckdb

For every wrong answer it first asks a mechanical question: is the agent's result exactly the gold query with some of the question's traps
(test accounts, IST dates, order status, refunds) switched off? If so, the agent behaved as if it did not know that definition. Anything
else is tagged by hand in analysis/manual_tags.yaml after reading the SQL. Refusals of answerable questions are counted on their own.

Writes results/failure_analysis.json (counts) and results/failure_analysis.md (one row per wrong answer).
"""
import argparse
import glob
import itertools
import json
import os
import re
import sys

import yaml

from gloss.evaluation import GOOD, MACROS, MUTATIONS, load_questions, results_match
from gloss.tools import Warehouse
from gloss.warehouse import run_query

HERE = os.path.dirname(os.path.abspath(__file__))
_MACRO = re.compile(r"\{([A-Z_]+)((?: [^{}]*)?)\}")


def expand_with(sql: str, muts: tuple[str, ...]) -> str:
    """Like gloss.evaluation.expand, but switches several traps off at once."""
    def sub(m):
        name, args = m.group(1), m.group(2).split()
        if name == "IST_RANGE":
            return f"{{IST_DATE}} BETWEEN DATE '{args[0]}' AND DATE '{args[1]}'"
        text, wrong = MACROS[name]
        for mu in muts:
            if mu in wrong:
                return wrong[mu]
        return text
    for _ in range(8):
        new = _MACRO.sub(sub, sql)
        if new == sql:
            break
        sql = new
    return " ".join(sql.split())


def explain(wh, q, rec) -> str | None:
    """The smallest set of switched-off traps whose gold result equals the agent's result, or None."""
    if rec["rows"] is None or (rec["n_rows"] or 0) > len(rec["rows"]):
        return None
    agent = [tuple(r) for r in rec["rows"]]
    for size in range(1, len(q.traps) + 1):
        for subset in itertools.combinations(q.traps, size):
            rows = run_query(wh.con, expand_with(q.gold, subset), wh.tables, max_rows=1000).rows
            if results_match(rows, agent, ordered=q.ordered, places=q.places):
                return "+".join(subset)
    return None


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """Two-sided 95% Wilson interval for k correct out of n: how far one 30-question score can be trusted."""
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return [round(centre - half, 3), round(centre + half, 3)]


def read_run(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [r for r in (json.loads(line) for line in f) if "id" in r]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gloss.duckdb")
    ap.add_argument("--results", default="results")
    args = ap.parse_args()

    wh = Warehouse.open(args.db)
    questions = {q.id: q for q in load_questions()}
    with open(os.path.join(HERE, "manual_tags.yaml"), encoding="utf-8") as f:
        manual = yaml.safe_load(f) or {}

    counts, table = {}, []
    for path in sorted(glob.glob(os.path.join(args.results, "*.jsonl"))):
        run = os.path.basename(path)[:-len(".jsonl")]
        c = {"n": 0, "correct": 0, "wrong_answer": 0, "false_refusal": 0, "should_have_refused": 0, "no_result": 0,
             "wrong_explained_by_missing_definitions": 0, "wrong_tagged_by_hand": 0, "wrong_untagged": 0}
        for rec in read_run(path):
            c["n"] += 1
            if rec["outcome"] in GOOD:
                c["correct"] += 1
                continue
            c[rec["outcome"]] += 1
            q = questions[rec["id"]]
            label, how = "", ""
            if rec["outcome"] == "wrong_answer":
                hand = manual.get(run, {}).get(rec["id"])
                switched = explain(wh, q, rec)
                if hand:
                    c["wrong_tagged_by_hand"] += 1
                    label, how = hand["category"] + ": " + " ".join(hand["note"].split()), "hand"
                elif switched:
                    c["wrong_explained_by_missing_definitions"] += 1
                    label, how = f"equals the gold query with these traps ignored: {switched}", "auto"
                else:
                    c["wrong_untagged"] += 1
                    label, how = "not yet tagged", "none"
            elif rec["outcome"] == "false_refusal":
                label, how = "refused: " + " ".join(rec["refusal_reason"].split())[:160], "auto"
            table.append((run, rec["id"], q.kind, rec["outcome"], how, label))
        c["accuracy"] = round(c["correct"] / c["n"], 4)
        c["accuracy_wilson95"] = wilson(c["correct"], c["n"])
        counts[run] = c

    with open(os.path.join(args.results, "failure_analysis.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(counts, f, indent=1, sort_keys=True)
        f.write("\n")
    lines = ["| run | id | kind | outcome | tagged | explanation |", "|---|---|---|---|---|---|"]
    lines += [f"| {r} | {i} | {k} | {o} | {h} | {l.replace('|', '/')} |" for r, i, k, o, h, l in table]
    with open(os.path.join(args.results, "failure_analysis.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    for run, c in counts.items():
        print(f"{run:<26} n={c['n']} correct={c['correct']} wrong={c['wrong_answer']} (missing-definition={c['wrong_explained_by_missing_definitions']}, "
              f"hand={c['wrong_tagged_by_hand']}, untagged={c['wrong_untagged']}) false_refusal={c['false_refusal']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
