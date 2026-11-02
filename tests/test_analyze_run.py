"""The run analyser: failures show the gold answer next to the agent's, and compare lines two runs up by question."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

from gloss.evaluation import expand, load_questions, run_gold
from gloss.tools import Warehouse

from fixtures import warehouse

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
spec = importlib.util.spec_from_file_location("analyze_run", os.path.join(ROOT, "scripts", "analyze_run.py"))
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


def record(qid, kind, outcome, **kw):
    base = {"id": qid, "kind": kind, "outcome": outcome, "sql": "", "error": "", "rows": None, "refusal_reason": "",
            "retrieved": ["rule:test_accounts"], "definitions_used": [], "assumptions": []}
    base.update(kw)
    return base


class TestAnalyze(unittest.TestCase):
    def write_run(self, tmp, name, recs):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"meta": {"split": "dev"}}) + "\n")      # the meta line has no id and must be skipped
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return path

    def test_failures_show_gold_next_to_the_agent_answer(self):
        db = warehouse()[0]
        wrong = record("d08", "definition", "wrong_answer", sql="SELECT COUNT(*) FROM orders WHERE status = 'pending'", rows=[[133]])
        right = record("d07", "definition", "correct")
        refused = record("u01", "unanswerable", "should_have_refused", sql="SELECT 1", rows=[[1]])
        with tempfile.TemporaryDirectory() as tmp:
            run = analyze.read_run(self.write_run(tmp, "dev_x.jsonl", [wrong, right, refused]))
            self.assertEqual(sorted(run), ["d07", "d08", "u01"])
            items = analyze.failures(run, db)
            self.assertEqual([i["id"] for i in items], ["d08", "u01"])
            q = next(x for x in load_questions() if x.id == "d08")
            wh = Warehouse.open(db)
            self.assertEqual(items[0]["gold_rows"], [list(r) for r in run_gold(wh.con, wh.tables, q).rows])
            self.assertEqual(items[0]["agent_rows"], [[133]])
            self.assertIsNone(items[1]["gold_rows"])
            self.assertIn("cost", items[1]["why_unanswerable"].lower())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                analyze.print_failures(items)
            self.assertIn("gold rows", buf.getvalue())
            md = os.path.join(tmp, "f.md")
            analyze.write_markdown(items, md)
            text = open(md, encoding="utf-8").read()
            self.assertIn("| d08 | definition | wrong_answer |", text)
            self.assertIn("Categories:", text)

    def test_no_failures(self):
        self.assertEqual(analyze.failures({"d07": record("d07", "definition", "correct")}, warehouse()[0]), [])

    def test_compare_lines_up_by_question(self):
        a = {"d01": record("d01", "definition", "correct"), "d02": record("d02", "definition", "correct")}
        b = {"d01": record("d01", "definition", "wrong_answer")}
        self.assertEqual(analyze.compare(a, b, "a", "b"), [("d01", "definition", "correct", "wrong_answer")])


if __name__ == "__main__":
    unittest.main()
