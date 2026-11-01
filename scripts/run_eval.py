"""Run the Gloss agent over one split in one configuration and score it. Needs GOOGLE_API_KEY.

    python scripts/run_eval.py --split dev --config definitions
    python scripts/run_eval.py --split dev --config none
    python scripts/run_eval.py --split test --config definitions --final      # only after `git tag frozen-v1`, once per configuration

Results go to results/<split>_<config>.jsonl, one line per question, so a run that stops (daily quota, closed laptop) resumes where it
stopped. Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY to see every run as a trace.

The test split is guarded: it refuses to run unless the tag frozen-v1 exists and src/, eval/, scripts/ and tests/ are exactly as tagged.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

from gloss.agent import CONFIGS, DEFAULT_MODEL, MAX_REPAIRS, SYSTEM_PROMPT, TOP_K, build_agent, gemini_planner, run_question, to_prediction
from gloss.evaluation import (GOLD_PATH, QUESTIONS_PATH, load_questions, run_gold, score, split_of, summarise)
from gloss.throttle import DailyQuotaExceeded, Throttle
from gloss.tools import Warehouse

TAG = "frozen-v1"
GUARDED = ("src", "eval", "scripts", "tests")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def check_freeze() -> None:
    """Fail closed: the test split may only be run on exactly the code and questions that were tagged."""
    try:
        tag_commit = git("rev-list", "-n", "1", TAG)
        head = git("rev-parse", "HEAD")
        git("merge-base", "--is-ancestor", tag_commit, head)
        changed = git("diff", "--name-only", TAG, "--", *GUARDED) + git("status", "--porcelain", "--", *GUARDED)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        sys.exit(f"Cannot confirm the freeze ({exc}). Create the tag with `git tag {TAG}` first.")
    if changed.strip():
        sys.exit(f"Files under {GUARDED} differ from tag {TAG}; the test split must run on the frozen code:\n{changed}")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def file_sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def read_records(path: str) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if "id" in row:
                    done[row["id"]] = row
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--config", choices=CONFIGS, required=True)
    ap.add_argument("--db", default="data/gloss.duckdb")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None, help="run only the first N unanswered questions (smoke test)")
    ap.add_argument("--final", action="store_true", help="required for the test split")
    args = ap.parse_args()

    if args.split == "test":
        if not args.final:
            sys.exit("The test split is frozen. Use --final, once per configuration, after tagging.")
        check_freeze()
    if not os.path.exists(args.db):
        sys.exit(f"{args.db} not found. Build it: python -m gloss.pipeline --out {args.db}")

    out = args.out or os.path.join("results", f"{args.split}_{args.config}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    questions = split_of(load_questions(), args.split)
    done = read_records(out)

    wh = Warehouse.open(args.db)
    counts = {t: wh.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in wh.tables}
    with open(GOLD_PATH, encoding="utf-8") as f:
        if json.load(f)["warehouse_rows"] != counts:
            sys.exit(f"{args.db} does not match the data the gold answers were made from: {counts}")

    throttle = Throttle()
    planner = gemini_planner(throttle=throttle)
    agent = build_agent(wh, planner)
    meta = {"meta": {"split": args.split, "config": args.config, "model_requested": planner.model, "default_model": DEFAULT_MODEL,
                     "top_k": TOP_K, "max_repairs": MAX_REPAIRS, "system_prompt_sha": sha(SYSTEM_PROMPT),
                     "questions_sha": file_sha(QUESTIONS_PATH), "gold_sha": file_sha(GOLD_PATH),
                     "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                     "git_head": git("rev-parse", "HEAD") if os.path.isdir(".git") else "", "warehouse_rows": counts}}
    todo = [q for q in questions if q.id not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{args.split}/{args.config}: {len(done)} done, {len(todo)} to run, model {planner.model}")

    with open(out, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(meta) + "\n")
        for n, q in enumerate(todo, 1):
            try:
                final = run_question(agent, q.question, args.config, qid=q.id, split=args.split, model=planner.model)
            except DailyQuotaExceeded as exc:
                print(f"\nDaily quota used up after {n - 1} questions this session: {exc}\nRun the same command again tomorrow; it resumes.")
                return 3
            gold = [tuple(r) for r in run_gold(wh.con, wh.tables, q).rows] if q.answerable else None
            outcome = score(q, gold, to_prediction(final))
            rec = {"id": q.id, "split": q.split, "kind": q.kind, "config": args.config, "outcome": outcome, "status": final["status"],
                   "refusal_reason": final["refusal_reason"], "sql": final["sql"], "error": final["last_error"],
                   "n_rows": len(final["result"]["rows"]) if final["result"] else None,
                   "rows": final["result"]["rows"][:20] if final["result"] else None, "retrieved": final["retrieved"],
                   "model_calls": final["model_calls"], "input_tokens": final["input_tokens"], "output_tokens": final["output_tokens"],
                   "latency_s": final["latency_s"], "models": sorted(set(final["models"])), "retries_waited": throttle.retries,
                   "definitions_used": (final["plan"] or {}).get("definitions_used", []),
                   "assumptions": (final["plan"] or {}).get("assumptions", [])}
            f.write(json.dumps(rec, default=str) + "\n")
            f.flush()
            done[q.id] = rec
            print(f"[{n}/{len(todo)}] {q.id:<4} {outcome:<20} calls={rec['model_calls']} {rec['latency_s']}s")

    if all(q.id in done for q in questions):
        summary = summarise(questions, {q.id: done[q.id]["outcome"] for q in questions})
        calls = [done[q.id]["model_calls"] for q in questions]
        summary["cost"] = {"model_calls_total": sum(calls), "model_calls_mean": round(sum(calls) / len(calls), 3),
                           "input_tokens_total": sum(done[q.id]["input_tokens"] for q in questions),
                           "output_tokens_total": sum(done[q.id]["output_tokens"] for q in questions),
                           "latency_s_mean": round(sum(done[q.id]["latency_s"] for q in questions) / len(questions), 2)}
        summary["models_seen"] = sorted({m for q in questions for m in done[q.id]["models"]})
        path = out.replace(".jsonl", ".summary.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(summary, f, indent=1, sort_keys=True)
            f.write("\n")
        print(f"\nwrote {path}\n{json.dumps(summary['by_kind'], indent=1)}\noverall {summary['overall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
