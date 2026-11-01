"""The evaluation runner, driven by a fake model: resume, summary, and the freeze guard on the test split."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

try:
    import langgraph  # noqa: F401
except ImportError:
    raise unittest.SkipTest("langgraph is not installed")

from gloss.agent import Plan, PlanResult
from gloss.evaluation import expand, load_questions

from fixtures import warehouse

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
spec = importlib.util.spec_from_file_location("run_eval", os.path.join(ROOT, "scripts", "run_eval.py"))
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)

QS = load_questions()


class Oracle:
    model = "fake-oracle"

    def __call__(self, messages):
        text = messages[1][1]
        question = text[text.rindex("Question: ") + len("Question: "):].split("\n\n")[0]
        q = next(x for x in QS if x.question == question)
        if not q.answerable:
            return PlanResult(Plan(answerable=False, sql="", definitions_used=[], assumptions=[], refusal_reason=q.why),
                              input_tokens=10, output_tokens=5, model="fake-oracle")
        return PlanResult(Plan(answerable=True, sql=expand(q.gold), definitions_used=list(q.needs), assumptions=[], refusal_reason=""),
                          input_tokens=10, output_tokens=5, model="fake-oracle")


class TestRunner(unittest.TestCase):
    def run_main(self, *argv):
        old = run_eval.gemini_planner
        run_eval.gemini_planner = lambda throttle=None: Oracle()
        import sys
        saved = sys.argv
        sys.argv = ["run_eval.py", *argv]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = run_eval.main()
        finally:
            sys.argv, run_eval.gemini_planner = saved, old
        return code, buf.getvalue()

    def test_resume_and_summary(self):
        db = warehouse()[0]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "dev_none.jsonl")
            code, _ = self.run_main("--split", "dev", "--config", "none", "--db", db, "--out", out, "--limit", "7")
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as f:
                lines = [json.loads(x) for x in f]
            self.assertEqual(len(lines), 1 + 7)                       # one meta line, seven questions
            self.assertEqual(lines[0]["meta"]["config"], "none")
            self.assertFalse(os.path.exists(out.replace(".jsonl", ".summary.json")))   # no summary for a partial run

            code, text = self.run_main("--split", "dev", "--config", "none", "--db", db, "--out", out)
            self.assertEqual(code, 0)
            self.assertIn("7 done, 23 to run", text)
            with open(out, encoding="utf-8") as f:
                records = [json.loads(x) for x in f if '"id"' in x]
            self.assertEqual(sorted(r["id"] for r in records), sorted(q.id for q in QS if q.split == "dev"))   # none run twice
            with open(out.replace(".jsonl", ".summary.json"), encoding="utf-8") as f:
                summary = json.load(f)
            self.assertEqual(summary["overall"], {"n": 30, "correct": 30, "rate": 1.0})
            self.assertEqual(summary["cost"]["model_calls_total"], 30)
            self.assertEqual(summary["models_seen"], ["fake-oracle"])

    def test_test_split_needs_final_and_a_freeze_tag(self):
        db = warehouse()[0]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "test_none.jsonl")
            with self.assertRaises(SystemExit) as no_final:
                self.run_main("--split", "test", "--config", "none", "--db", db, "--out", out)
            self.assertIn("frozen", str(no_final.exception))
            with self.assertRaises(SystemExit) as no_tag:
                self.run_main("--split", "test", "--config", "none", "--db", db, "--out", out, "--final")
            self.assertIn("freeze", str(no_tag.exception).lower())
            self.assertFalse(os.path.exists(out))                      # nothing was run or written


if __name__ == "__main__":
    unittest.main()
