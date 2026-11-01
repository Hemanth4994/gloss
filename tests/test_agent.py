"""The agent graph, tested with a scripted fake model: no API key, no network.
These tests check the plumbing and the safety limits. They say nothing about how good the real model is; that is the evaluation."""
import re
import unittest

try:
    import langgraph  # noqa: F401  (the agent extra: pip install -e ".[agent]")
except ImportError:
    raise unittest.SkipTest("langgraph is not installed")

from gloss.agent import (MAX_REPAIRS, SYSTEM_PROMPT, Plan, PlanResult, build_agent, build_messages, render_answer, run_question,
                         schema_text, to_prediction)
from gloss.dictionary import chunks
from gloss.evaluation import expand, load_questions, results_match, run_gold, score, summarise
from gloss.tools import Warehouse
from gloss.warehouse import table_names

from fixtures import warehouse

QS = load_questions()
BY_ID = {q.id: q for q in QS}


def ok(sql, used=(), assume=()):
    return PlanResult(Plan(answerable=True, sql=sql, definitions_used=list(used), assumptions=list(assume), refusal_reason=""),
                      input_tokens=100, output_tokens=20, model="fake-model")


def refuse(reason="not stored"):
    return PlanResult(Plan(answerable=False, sql="", definitions_used=[], assumptions=[], refusal_reason=reason),
                      input_tokens=100, output_tokens=10, model="fake-model")


class Script:
    """A fake model that returns its results in order (the last one repeats) and remembers the prompts it was given."""

    def __init__(self, *results):
        self.results, self.prompts = list(results), []

    def __call__(self, messages):
        self.prompts.append(messages)
        r = self.results[min(len(self.prompts) - 1, len(self.results) - 1)]
        return r(messages) if callable(r) else r


class AgentCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = warehouse()[0]
        cls.wh = Warehouse.open(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.wh.con.close()

    def run_with(self, script, question="How many orders are still pending?", config="definitions", **kw):
        return run_question(build_agent(self.wh, script, **kw), question, config)


class TestPromptsAndRetrieval(AgentCase):
    def test_the_system_prompt_carries_no_business_rule(self):
        """If the shared prompt hinted at a rule, the 'none' configuration would not be a fair baseline."""
        low = SYSTEM_PROMPT.lower()
        for word in ("test account", "is_test", "ist", "utc", "refund", "net revenue", "cancelled", "returned", "pending", "status", "gmv", "revenue"):
            self.assertNotRegex(low, r"\b" + re.escape(word) + r"\b", word)

    def test_schema_is_bare_names_and_types(self):
        text = schema_text(self.wh)
        self.assertIn("orders(order_id", text)
        self.assertIn("is_test BOOLEAN", text)
        self.assertNotIn("internal", text.lower())

    def test_definitions_config_shows_every_rule_plus_the_bm25_top_chunks(self):
        script = Script(ok("SELECT 1"))
        final = self.run_with(script, "What was net revenue in August 2026?")
        rules = [c.id for c in chunks() if c.kind == "rule"]
        self.assertEqual(final["retrieved"][:len(rules)], rules)
        self.assertIn("metric:net_revenue", final["retrieved"])
        shown = script.prompts[0][1][1]
        self.assertIn("Definitions:", shown)
        self.assertIn("[rule:test_accounts]", shown)

    def test_none_config_shows_no_definition_at_all(self):
        script = Script(ok("SELECT 1"))
        final = self.run_with(script, "What was net revenue in August 2026?", config="none")
        self.assertEqual(final["retrieved"], [])
        self.assertNotIn("Definitions:", script.prompts[0][1][1])
        self.assertNotIn("test_accounts", script.prompts[0][1][1])

    def test_the_two_configs_differ_only_in_the_definitions_block(self):
        a, b = Script(ok("SELECT 1")), Script(ok("SELECT 1"))
        self.run_with(a, config="definitions")
        self.run_with(b, config="none")
        self.assertEqual(a.prompts[0][0], b.prompts[0][0])
        with_defs = a.prompts[0][1][1]
        self.assertEqual(with_defs.replace(with_defs[with_defs.index("Definitions:"):with_defs.index("Question:")], ""), b.prompts[0][1][1])

    def test_unknown_config_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_with(Script(ok("SELECT 1")), config="everything")


class TestGraphBehaviour(AgentCase):
    def test_happy_path_is_one_model_call_and_a_rendered_answer(self):
        script = Script(ok("SELECT COUNT(*) AS pending FROM orders WHERE status = 'pending'", used=["rule:order_status", "rule:nope"],
                           assume=["counted all customers"]))
        final = self.run_with(script)
        self.assertEqual((final["status"], final["model_calls"]), ("answered", 1))
        self.assertEqual(final["result"]["rows"], [[133]])
        self.assertEqual((final["input_tokens"], final["output_tokens"], final["models"]), (100, 20, ["fake-model"]))
        self.assertIn("pending: 133", final["answer"])
        self.assertIn("order status (rule:order_status)", final["answer"])
        self.assertIn("unknown id (rule:nope)", final["answer"])          # a made-up id is shown as unknown, never as a real definition
        self.assertIn("SQL: SELECT COUNT(*)", final["answer"])
        self.assertIn("Assumptions: counted all customers", final["answer"])

    def test_a_failing_query_is_repaired_using_the_error_message(self):
        script = Script(ok("SELECT COUNT(*) FROM orders WHERE nope = 1"), ok("SELECT COUNT(*) AS n FROM orders"))
        final = self.run_with(script)
        self.assertEqual((final["status"], final["model_calls"]), ("answered", 2))
        repair = script.prompts[1][1][1]
        self.assertIn("SELECT COUNT(*) FROM orders WHERE nope = 1", repair)
        self.assertIn("QueryError", repair)
        self.assertIn("nope", repair)

    def test_a_refused_query_is_repaired_the_same_way_and_nothing_runs(self):
        script = Script(ok("DROP TABLE orders"), ok("SELECT COUNT(*) AS n FROM orders"))
        final = self.run_with(script)
        self.assertEqual(final["status"], "answered")
        self.assertIn("GuardError", script.prompts[1][1][1])
        self.assertIn("orders", table_names(self.wh.con))

    def test_the_model_is_called_at_most_three_times(self):
        script = Script(ok("DROP TABLE orders"))
        final = self.run_with(script)
        self.assertEqual((final["status"], final["model_calls"]), ("failed", 1 + MAX_REPAIRS))
        self.assertEqual(len(script.prompts), 3)
        self.assertTrue(final["answer"].startswith("No answer"))
        self.assertEqual(to_prediction(final).rows, None)

    def test_zero_repairs_means_one_call(self):
        script = Script(ok("SELECT nope FROM orders"))
        final = self.run_with(script, max_repairs=0)
        self.assertEqual((final["status"], final["model_calls"]), ("failed", 1))

    def test_an_unparseable_reply_gets_another_try(self):
        script = Script(PlanResult(None, error="bad json", model="fake-model"), ok("SELECT COUNT(*) AS n FROM orders"))
        final = self.run_with(script)
        self.assertEqual((final["status"], final["model_calls"]), ("answered", 2))
        self.assertIn("not a valid answer", script.prompts[1][1][1])

    def test_answerable_with_empty_sql_is_treated_as_an_error(self):
        final = self.run_with(Script(ok("   "), ok("SELECT COUNT(*) AS n FROM orders")))
        self.assertEqual((final["status"], final["model_calls"]), ("answered", 2))

    def test_a_refusal_stops_immediately_and_runs_no_sql(self):
        script = Script(refuse("no cost data"))
        final = self.run_with(script, "What is our profit margin by product category?")
        self.assertEqual((final["status"], final["model_calls"], final["refusal_reason"]), ("refused", 1, "no cost data"))
        self.assertIsNone(final["result"])
        self.assertTrue(to_prediction(final).refused)

    def test_steps_record_what_happened_for_the_trace(self):
        final = self.run_with(Script(ok("SELECT nope FROM orders"), ok("SELECT COUNT(*) AS n FROM orders")))
        self.assertEqual([s["node"] for s in final["steps"]], ["retrieve", "plan", "execute", "plan", "execute"])
        self.assertIn("error", final["steps"][2])

    def test_render_answer_for_many_rows_is_capped(self):
        final = self.run_with(Script(ok("SELECT order_id, status FROM orders ORDER BY order_id LIMIT 40")))
        self.assertIn("showing 15 of 40", final["answer"])


class TestEndToEndScoring(AgentCase):
    """An oracle model that replays each question's gold SQL must score 100% through the whole graph, in both configs."""

    @staticmethod
    def oracle(messages):
        text = messages[1][1]
        question = text[text.rindex("Question: ") + len("Question: "):].split("\n\n")[0]
        q = next(x for x in QS if x.question == question)
        return refuse(q.why) if not q.answerable else ok(expand(q.gold), used=list(q.needs))

    def test_oracle_scores_everything_through_the_graph(self):
        con, tables = self.wh.con, self.wh.tables
        for config in ("definitions", "none"):
            agent = build_agent(self.wh, self.oracle)
            outcomes = {}
            for q in QS:
                final = run_question(agent, q.question, config, qid=q.id, split=q.split)
                gold = [tuple(r) for r in run_gold(con, tables, q).rows] if q.answerable else None
                outcomes[q.id] = score(q, gold, to_prediction(final))
            s = summarise(QS, outcomes)
            self.assertEqual(s["overall"]["rate"], 1.0, (config, s["outcome_counts"]))

    def test_a_model_that_ignores_the_rules_is_caught_by_the_scorer(self):
        con, tables = self.wh.con, self.wh.tables
        q = BY_ID["d07"]
        wrong = ok(expand(q.gold, "test"))
        final = self.run_with(Script(wrong), q.question)
        gold = [tuple(r) for r in run_gold(con, tables, q).rows]
        self.assertEqual(score(q, gold, to_prediction(final)), "wrong_answer")

    def test_json_dates_in_agent_rows_match_gold_dates(self):
        q = BY_ID["j04"]
        final = self.run_with(Script(ok(expand(q.gold))), q.question)
        self.assertIsInstance(final["result"]["rows"][0][0], str)
        gold = run_gold(self.wh.con, self.wh.tables, q).rows
        self.assertTrue(results_match(gold, to_prediction(final).rows, q.ordered, q.places))
        self.assertTrue(results_match([("2026-01-01", 1)], [("2026-01-01T00:00:00", 1)]))


class TestRunMetadata(AgentCase):
    def test_run_question_reports_latency_and_calls(self):
        final = self.run_with(Script(ok("SELECT COUNT(*) AS n FROM orders")))
        self.assertGreaterEqual(final["latency_s"], 0)
        self.assertEqual(final["model_calls"], 1)

    def test_build_messages_includes_error_only_on_repair(self):
        first = build_messages("t(a int)", "", "q?")
        again = build_messages("t(a int)", "", "q?", "SELECT 1", "boom")
        self.assertNotIn("did not work", first[1][1])
        self.assertIn("boom", again[1][1])


if __name__ == "__main__":
    unittest.main()
