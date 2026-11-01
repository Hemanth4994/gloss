"""Does retrieval bring back the definitions a question needs? No model, no API key.

It runs on the DEV questions. The test split is frozen: it is read only with --final, once, after the settings are tagged.
    PYTHONPATH=src python scripts/eval_retrieval.py                  # dev
    PYTHONPATH=src python scripts/eval_retrieval.py --split test --final

Strategies compared:
    bm25          the top k chunks for the question text
    rules+bm25    every rule chunk always, plus the top k chunks (rules are short and apply almost everywhere)
Cost is the average number of words the agent would have to read.
"""
import argparse
import json
import os
import sys

from gloss.dictionary import chunks
from gloss.evaluation import evaluate_retrieval, load_questions, split_of
from gloss.retrieval import Retriever


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--final", action="store_true", help="required to read the frozen test split")
    ap.add_argument("--out", default=None, help="write the full result as JSON (default results/retrieval_<split>.json)")
    args = ap.parse_args()
    if args.split == "test" and not args.final:
        print("The test split is frozen. Use --final only once, after the settings are tagged.", file=sys.stderr)
        return 2

    questions = split_of(load_questions(), args.split)
    all_chunks = chunks()
    retriever = Retriever(all_chunks)
    rule_ids = [c.id for c in all_chunks if c.kind == "rule"]

    def bm25(k):
        return lambda text: [h.chunk.id for h in retriever.search(text, k)]

    def rules_plus(k):
        return lambda text: rule_ids + [i for i in bm25(k)(text) if i not in rule_ids]

    strategies = {f"bm25 k={k}": bm25(k) for k in (1, 3, 5)}
    strategies.update({f"rules+bm25 k={k}": rules_plus(k) for k in (1, 3)})

    results = {name: evaluate_retrieval(questions, fn) for name, fn in strategies.items()}
    n = next(iter(results.values()))["questions"]
    print(f"{args.split} split: {n} answerable questions that need at least one definition\n")
    print(f"{'strategy':<18}{'mean recall':>12}{'all needed':>12}{'rule recall':>13}{'metric recall':>15}{'avg words':>11}")
    for name, r in results.items():
        print(f"{name:<18}{r['mean_recall']:>12.3f}{r['complete']:>12.3f}{r['rule_recall']:>13.3f}{r['metric_recall']:>15.3f}{r['avg_words']:>11.1f}")

    misses: dict[str, int] = {}
    for row in results["bm25 k=3"]["per_question"]:
        for m in row["missed"]:
            misses[m] = misses.get(m, 0) + 1
    print("\nmost-missed chunks for bm25 k=3 (questions that needed it but did not get it):")
    for chunk_id, count in sorted(misses.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {chunk_id:<32}{count}")

    out = args.out or os.path.join("results", f"retrieval_{args.split}.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"split": args.split, "retriever": {"type": "bm25", "k1": retriever.k1, "b": retriever.b}, "results": results},
                  f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
