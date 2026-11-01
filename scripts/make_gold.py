"""Rebuild the warehouse from the fixed seed, run every gold query and write eval/gold_answers.json.

Run this after changing a gold query or the generator, then read the diff: every changed answer is a decision you must be able to explain.
    PYTHONPATH=src python scripts/make_gold.py
"""
import json
import os
import sys
import tempfile

from gloss.evaluation import GOLD_PATH, expand, load_questions, run_gold, snapshot
from gloss.pipeline import build_warehouse
from gloss.warehouse import connect_readonly, table_names


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gloss.duckdb")
        build_warehouse(path)
        con = connect_readonly(path)
        tables = table_names(con)
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        answers = {}
        for q in load_questions():
            if q.answerable:
                answers[q.id] = {"question": q.question, "sql": expand(q.gold), **snapshot(run_gold(con, tables, q))}
        con.close()
    with open(GOLD_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"warehouse_rows": counts, "answers": answers}, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(answers)} gold answers to {os.path.relpath(GOLD_PATH)}; warehouse rows {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
