"""Write space/results_summary.json, the numbers shown on the Evaluation tab of the demo, straight from results/test_*.jsonl.

    python analysis/make_space_summary.py

Nothing is typed in by hand: counts come from the per-question records, intervals are 95% Wilson intervals.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIGS = ("definitions", "tables", "none")
KINDS = ("definition", "join", "control", "unanswerable")
GOOD = ("correct", "correct_refusal")
NOTE = ("The questions, the gold SQL and the definitions were all written by the same author, and the questions were written to depend on the "
        "definitions, so the gap shows that the agent uses supplied definitions correctly, not that it can discover a company's conventions. "
        "30 questions per configuration is a small sample, and this model ignores the temperature setting, so each number is one run. "
        "The data is synthetic.")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    p, d = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(centre - half, 4), round(centre + half, 4)]


def read_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [r for r in (json.loads(line) for line in f if line.strip()) if "id" in r]


def summarise(rows: list[dict], config: str) -> dict:
    ok = [r for r in rows if r["outcome"] in GOOD]
    return {"config": config, "n": len(rows), "correct": len(ok), "wilson95": wilson(len(ok), len(rows)),
            "by_kind": {k: f"{sum(r['kind'] == k for r in ok)} / {sum(r['kind'] == k for r in rows)}" for k in KINDS}}


def build(results_dir: str) -> dict:
    out = []
    for c in CONFIGS:
        rows = read_records(os.path.join(results_dir, f"test_{c}.jsonl"))
        if len({r["id"] for r in rows}) != len(rows) or any(r["split"] != "test" for r in rows):
            raise ValueError(f"test_{c}.jsonl: duplicate ids or a non-test record")
        out.append(summarise(rows, c))
    return {"test": out, "note": NOTE}


def main() -> None:
    data = build(os.path.join(ROOT, "results"))
    path = os.path.join(ROOT, "space", "results_summary.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    print(f"wrote {path}")
    for c in data["test"]:
        print(c["config"], f"{c['correct']} / {c['n']}", c["by_kind"])


if __name__ == "__main__":
    main()
