"""Checks for the post-hoc analysis: run with  PYTHONPATH=src:analysis:tests python -m unittest analysis/test_classify_failures.py"""
import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_failures as cf  # noqa: E402

from gloss.evaluation import expand, load_questions  # noqa: E402
from gloss.tools import Warehouse  # noqa: E402

from fixtures import warehouse  # noqa: E402

CATEGORIES = {"fan-out", "wrong aggregation level", "clock instead of as-of date", "new customer from signup",
              "metric defined differently", "returns from refunds"}


class TestClassify(unittest.TestCase):
    def test_wilson_matches_known_values(self):
        self.assertEqual(cf.wilson(27, 30), [0.744, 0.965])
        self.assertEqual(cf.wilson(10, 30), [0.192, 0.512])
        lo, hi = cf.wilson(0, 30)
        self.assertEqual(lo, 0.0)
        self.assertLess(hi, 0.15)

    def test_single_trap_expansion_agrees_with_the_evaluation_module(self):
        for q in load_questions():
            for trap in q.traps:
                self.assertEqual(cf.expand_with(q.gold, (trap,)), expand(q.gold, trap), f"{q.id} {trap}")
            if q.answerable:
                self.assertEqual(cf.expand_with(q.gold, ()), expand(q.gold), q.id)

    def test_explain_finds_the_traps_an_agent_ignored(self):
        wh = Warehouse.open(warehouse()[0])
        q = next(x for x in load_questions() if x.id == "d07")                 # orders on 15 Aug: traps tz and test
        wrong = cf.expand_with(q.gold, ("tz", "test"))
        from gloss.warehouse import run_query
        rows = run_query(wh.con, wrong, wh.tables).rows
        rec = {"rows": [list(r) for r in rows], "n_rows": len(rows)}
        self.assertEqual(cf.explain(wh, q, rec), "test+tz")
        self.assertIsNone(cf.explain(wh, q, {"rows": [[-1]], "n_rows": 1}))
        self.assertIsNone(cf.explain(wh, q, {"rows": None, "n_rows": None}))

    def test_manual_tags_point_at_real_questions_and_known_categories(self):
        with open(os.path.join(os.path.dirname(cf.__file__), "manual_tags.yaml"), encoding="utf-8") as f:
            tags = yaml.safe_load(f)
        ids = {q.id for q in load_questions()}
        results = os.path.join(os.path.dirname(os.path.dirname(cf.__file__)), "results")
        for run, per in tags.items():
            self.assertTrue(os.path.exists(os.path.join(results, run + ".jsonl")), run)
            for qid, tag in per.items():
                self.assertIn(qid, ids)
                self.assertIn(tag["category"], CATEGORIES)
                self.assertTrue(tag["note"])


if __name__ == "__main__":
    unittest.main()
