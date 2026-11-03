"""The deployment app: rate limiting, the shared daily budget, key handling and table shape. No model is called."""
import asyncio
import importlib.util
import os
import sys
import unittest

try:
    import gradio  # noqa: F401
    import langgraph  # noqa: F401
except ImportError:
    raise unittest.SkipTest("gradio or langgraph is not installed")

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from fixtures import warehouse  # noqa: E402

os.environ["GLOSS_DB"] = warehouse()[0]
os.environ.pop("GOOGLE_API_KEY", None)
spec = importlib.util.spec_from_file_location("space_app", os.path.join(ROOT, "space", "app.py"))
space = importlib.util.module_from_spec(spec)
spec.loader.exec_module(space)


class TestTools(unittest.TestCase):
    def test_run_sql_keeps_column_names_and_refuses_writes(self):
        t, note = space.run_sql("SELECT status, COUNT(*) AS orders FROM orders GROUP BY status ORDER BY status")
        self.assertEqual(t["headers"], ["status", "orders"])
        self.assertEqual(len(t["data"]), 4)
        self.assertIn("rows", note)
        t, note = space.run_sql("DROP TABLE orders")
        self.assertEqual(t["data"], [])
        self.assertIn("only SELECT", note)

    def test_search_returns_the_rules_a_question_implies_by_name(self):
        self.assertIn("net_revenue", space.search("What was net revenue in August 2026?", 3))
        self.assertIn("Type a question", space.search("   "))

    def test_oversized_input_is_cut(self):
        out = space.search("net revenue " * 500, 3)
        self.assertIn("net_revenue", out)


class TestAgentTab(unittest.TestCase):
    def test_without_any_key_the_agent_says_so_and_calls_no_model(self):
        text, sql, rows, details = space.ask("What was net revenue in August 2026?", "definitions", "")
        self.assertIn("no model key", text)
        self.assertEqual((sql, details), ("", {}))
        self.assertEqual(rows["data"], [])

    def test_unknown_configuration_and_empty_question(self):
        self.assertEqual(space.ask("", "definitions", "")[0], "Type a question first.")
        self.assertEqual(space.ask("x", "everything", "")[0], "Unknown configuration.")

    def test_shared_budget_is_capped_per_day_and_resets(self):
        space._budget.update(day=None, used=0)
        taken = [space.take_owner_budget() for _ in range(space.DAILY_CAP + 3)]
        self.assertEqual(sum(taken), space.DAILY_CAP)
        space._budget["day"] = None                                  # a new day
        self.assertTrue(space.take_owner_budget())

    def with_planner(self, plan):
        original = space.planner_for
        space.planner_for = lambda key, throttle, model=space.DEFAULT_MODEL: (lambda messages: space.PlanResult(plan, model="fake", input_tokens=7, output_tokens=3))
        try:
            return space.ask("How many orders are there?", "definitions", "any-visitor-key")
        finally:
            space.planner_for = original

    def test_an_answered_question_returns_text_sql_named_columns_and_details(self):
        text, sql, rows, details = self.with_planner(space.Plan(
            answerable=True, sql="SELECT COUNT(*) AS n FROM orders", definitions_used=["rule:test_accounts"], assumptions=[], refusal_reason=""))
        self.assertIn("n: 5740", text)
        self.assertNotIn("SQL:", text)                               # the SQL has its own box
        self.assertEqual(sql, "SELECT COUNT(*) AS n FROM orders")
        self.assertEqual(rows, {"headers": ["n"], "data": [[5740]]})
        self.assertEqual(details["status"], "answered")
        self.assertEqual(details["tokens in/out"], [7, 3])

    def test_a_refusal_is_shown_with_its_reason(self):
        text, sql, rows, details = self.with_planner(space.Plan(
            answerable=False, sql="", definitions_used=[], assumptions=[], refusal_reason="There is no cost data."))
        self.assertEqual(text, "Cannot answer from this data: There is no cost data.")
        self.assertEqual((sql, details["status"]), ("", "refused"))

    def test_a_failing_key_is_not_echoed_back(self):
        secret = "AIza-secret-key-123"
        original = space.planner_for

        def broken(key, throttle, model=space.DEFAULT_MODEL):
            def planner(messages):
                raise RuntimeError(f"401 invalid key {key}")
            return planner
        space.planner_for = broken
        try:
            text, *_ = space.ask("What was net revenue in August 2026?", "definitions", secret)
        finally:
            space.planner_for = original
        self.assertIn("The model call failed", text)
        self.assertNotIn(secret, text)


class TestRateLimit(unittest.TestCase):
    def call(self, app, path="/mcp", client="1.2.3.4", headers=()):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(msg):
            sent.append(msg)
        scope = {"type": "http", "path": path, "headers": list(headers), "client": (client, 1)}
        asyncio.run(app(scope, receive, send))
        return sent[0]["status"]

    def make(self, limit=3):
        clock = {"t": 0.0}

        async def ok(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
        return space.RateLimit(ok, limit=limit, window=60.0, clock=lambda: clock["t"]), clock

    def test_limits_one_client_not_another_and_only_mcp(self):
        app, clock = self.make(3)
        self.assertEqual([self.call(app) for _ in range(4)], [200, 200, 200, 429])
        self.assertEqual(self.call(app, client="5.6.7.8"), 200)
        self.assertEqual(self.call(app, path="/"), 200)             # the web page is not limited here
        clock["t"] = 61.0
        self.assertEqual(self.call(app), 200)                        # the window moved on

    def test_uses_the_forwarded_address_behind_a_proxy(self):
        app, _ = self.make(1)
        h = [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")]
        self.assertEqual([self.call(app, headers=h), self.call(app, headers=h)], [200, 429])
        self.assertEqual(self.call(app, headers=[(b"x-forwarded-for", b"8.8.8.8")]), 200)


class TestResultsTab(unittest.TestCase):
    def test_results_markdown_matches_the_raw_records(self):
        import json
        sys.path.insert(0, os.path.join(ROOT, "analysis"))
        import make_space_summary as m
        data = m.build(os.path.join(ROOT, "results"))
        with open(os.path.join(ROOT, "space", "results_summary.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)                     # the file on disk is current
        text = space.results_markdown()
        self.assertIn("27 / 30", text)
        self.assertIn("definitions", text)


if __name__ == "__main__":
    unittest.main()
