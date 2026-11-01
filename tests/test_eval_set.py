"""The evaluation set must be sound before any model is run on it:
every gold query runs through the same guarded runner the agent uses, every listed trap really changes the answer,
the scorer accepts the gold answers and rejects the wrong ones, and the stored answer key still matches the data."""
import json
import unittest

from gloss.dictionary import chunks
from gloss.evaluation import (GOLD_PATH, KINDS, MUTATIONS, SPLITS, Prediction, Question, evaluate_retrieval, expand,
                              load_questions, recall, results_match, run_gold, score, snapshot, split_of, summarise)
from gloss.warehouse import connect_readonly, table_names

from fixtures import warehouse

QS = load_questions()


class TestQuestionFile(unittest.TestCase):
    def test_sixty_questions_split_evenly_with_the_same_mix(self):
        self.assertEqual(len(QS), 60)
        for split in SPLITS:
            part = split_of(QS, split)
            self.assertEqual(len(part), 30, split)
            self.assertEqual({k: sum(q.kind == k for q in part) for k in KINDS},
                             {"definition": 12, "join": 8, "control": 5, "unanswerable": 5}, split)

    def test_no_question_is_copied_from_the_dictionary(self):
        texts = [c.text.lower() for c in chunks()]
        for q in QS:
            self.assertFalse(any(q.question.lower() in t for t in texts), q.id)

    def test_definition_and_join_questions_need_something_controls_need_nothing(self):
        for q in QS:
            if q.kind in ("definition", "join"):
                self.assertTrue(q.needs and q.traps, q.id)
            if q.kind == "control":
                self.assertFalse(q.needs or q.traps, q.id)


class TestExpand(unittest.TestCase):
    def test_macros_expand_fully_and_mutations_differ(self):
        sql = "SELECT 1 FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {REAL} AND {IST_RANGE 2026-08-01 2026-08-31}"
        good, wrong = expand(sql), expand(sql, "test")
        self.assertNotIn("{", good)
        self.assertIn("NOT c.is_test", good)
        self.assertNotIn("NOT c.is_test", wrong)
        self.assertIn("INTERVAL 330 MINUTE", good)
        self.assertNotIn("INTERVAL 330 MINUTE", expand(sql, "tz"))

    def test_bad_input_is_refused(self):
        with self.assertRaises(KeyError):
            expand("SELECT {NOPE}")
        with self.assertRaises(ValueError):
            expand("SELECT {IST_RANGE 2026-08-01}")
        with self.assertRaises(ValueError):
            expand("SELECT 1", "bogus")


class TestGold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path, _, _, _ = warehouse()
        cls.con = connect_readonly(path)
        cls.tables = table_names(cls.con)
        cls.gold = {q.id: run_gold(cls.con, cls.tables, q) for q in QS if q.answerable}

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_every_gold_query_runs_through_the_guard_and_returns_a_usable_result(self):
        self.assertEqual(len(self.gold), 50)
        for qid, res in self.gold.items():
            self.assertTrue(res.rows, qid)
            self.assertFalse(res.truncated, qid)
            self.assertLess(len(res.rows), 200, qid)
            self.assertTrue(any(v is not None for row in res.rows for v in row), f"{qid}: all NULL")

    def test_listed_traps_are_exactly_the_mutations_that_change_the_answer(self):
        """A trap that does not change the answer is not tested, and an unlisted one would go unnoticed."""
        for q in QS:
            if not q.answerable:
                continue
            base = expand(q.gold)
            sensitive = set()
            for m in MUTATIONS:
                if expand(q.gold, m) == base:
                    continue            # this mutation does not apply to the question
                wrong = run_gold(self.con, self.tables, q, m)
                if not results_match(self.gold[q.id].rows, wrong.rows, q.ordered, q.places):
                    sensitive.add(m)
            self.assertEqual(sensitive, set(q.traps), q.id)

    def test_needs_agree_with_the_traps_that_were_measured(self):
        for q in QS:
            if q.answerable:
                self.assertEqual("rule:test_accounts" in q.needs, "test" in q.traps, q.id)
                self.assertEqual("rule:reporting_timezone" in q.needs, "tz" in q.traps, q.id)

    def test_controls_are_not_answered_by_applying_a_rule(self):
        with_anti = [q for q in QS if q.kind == "control" and q.anti]
        self.assertEqual({q.id for q in with_anti}, {"c01", "c05", "c06", "c09"})   # every raw row count that a rule would tempt to filter
        for q in with_anti:
            wrong = run_gold(self.con, self.tables, q, sql=q.anti)
            self.assertFalse(results_match(self.gold[q.id].rows, wrong.rows, q.ordered, q.places), q.id)

    def test_unanswerable_questions_name_things_the_schema_really_lacks(self):
        words = set()
        for name, col in self.con.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'main'").fetchall():
            words.update(name.lower().split("_"))
            words.update(col.lower().split("_"))
        for q in QS:
            if not q.answerable:
                self.assertFalse(set(q.missing) & words, f"{q.id}: the schema has {set(q.missing) & words}")

    def test_stored_answer_key_matches_the_data(self):
        """eval/gold_answers.json is committed so a change to the data or the gold SQL cannot go unnoticed."""
        with open(GOLD_PATH, encoding="utf-8") as f:
            key = json.load(f)
        self.assertEqual(set(key["answers"]), set(self.gold))
        for q in QS:
            if not q.answerable:
                continue
            stored, fresh = key["answers"][q.id], snapshot(self.gold[q.id])
            self.assertEqual(stored["sql"], expand(q.gold), q.id)
            self.assertEqual(stored["columns"], fresh["columns"], q.id)
            self.assertTrue(results_match(stored["rows"], fresh["rows"], q.ordered, places=6), q.id)

    def test_top_n_questions_have_no_tie_at_the_cut(self):
        """If rank N and rank N+1 tie, the 'right' list would depend on an arbitrary tie-break."""
        ties = {"d10": "WITH {ITEM_TOTALS}, {REFUND_TOTALS}, ltv AS (SELECT o.customer_id, SUM({NET_VALUE}) AS ltv {NET_FROM} "
                       "WHERE {COMPLETED} AND {REAL} GROUP BY o.customer_id) SELECT ltv FROM ltv ORDER BY ltv DESC LIMIT 2 OFFSET 4",
                "j08": "WITH per AS (SELECT o.customer_id, c.city {ORDERS_C} WHERE {COMPLETED} AND {REAL} "
                       "GROUP BY o.customer_id, c.city HAVING COUNT(*) >= 2) "
                       "SELECT COUNT(*) AS n FROM per GROUP BY city ORDER BY n DESC LIMIT 2 OFFSET 2"}
        for qid, sql in ties.items():
            rows = self.con.execute(expand(sql)).fetchall()
            self.assertEqual(len(rows), 2, qid)
            self.assertNotEqual(rows[0][0], rows[1][0], f"{qid}: tie at the cut")


class TestMatcher(unittest.TestCase):
    def test_integers_must_match_exactly_floats_within_the_stated_places(self):
        self.assertTrue(results_match([(100,)], [(100,)]))
        self.assertFalse(results_match([(100,)], [(101,)]))
        self.assertFalse(results_match([(100,)], [(100.4,)]))
        self.assertTrue(results_match([(0.0763,)], [(0.07634,)], places=4))
        self.assertFalse(results_match([(0.0763,)], [(0.0773,)], places=4))
        self.assertTrue(results_match([(2368.0728,)], [(2368.07,)], places=2))

    def test_column_names_and_order_do_not_matter_extra_columns_are_allowed(self):
        gold = [("a", 1), ("b", 2)]
        self.assertTrue(results_match(gold, [(1, "a"), (2, "b")]))
        self.assertTrue(results_match(gold, [("a", 1, 99), ("b", 2, 99)]))
        self.assertFalse(results_match(gold, [("a",), ("b",)]))
        self.assertFalse(results_match(gold, [("a", 1), ("b", 3)]))

    def test_row_order_counts_only_when_ordered(self):
        gold = [("a", 1), ("b", 2)]
        self.assertTrue(results_match(gold, [("b", 2), ("a", 1)], ordered=False))
        self.assertFalse(results_match(gold, [("b", 2), ("a", 1)], ordered=True))

    def test_row_count_and_missing_rows(self):
        self.assertFalse(results_match([(1,), (2,)], [(1,)]))
        self.assertFalse(results_match([(1,)], []))
        self.assertTrue(results_match([], []))

    def test_dates_and_none(self):
        import datetime as dt
        self.assertTrue(results_match([(dt.date(2026, 1, 1), 5)], [(dt.datetime(2026, 1, 1, 0, 0), 5)]))
        self.assertFalse(results_match([(dt.date(2026, 1, 1), 5)], [(dt.date(2026, 2, 1), 5)]))
        self.assertTrue(results_match([(None,)], [(None,)]))
        self.assertFalse(results_match([(0,)], [(None,)]))
        self.assertFalse(results_match([(1,)], [(True,)]))           # a boolean is not a number
        self.assertFalse(results_match([("1",)], [(1,)]))

    def test_too_many_columns_are_not_searched(self):
        self.assertFalse(results_match([(1,)], [tuple(range(20))]))


class TestScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path, _, _, _ = warehouse()
        con = connect_readonly(path)
        tables = table_names(con)
        cls.gold = {q.id: [tuple(r) for r in run_gold(con, tables, q).rows] for q in QS if q.answerable}
        cls.wrong = {q.id: [tuple(r) for r in run_gold(con, tables, q, q.traps[0]).rows] for q in QS if q.answerable and q.traps}
        con.close()

    def outcomes(self, make):
        return {q.id: score(q, self.gold.get(q.id), make(q)) for q in QS}

    def test_an_oracle_that_replays_the_gold_sql_gets_everything_right(self):
        s = summarise(QS, self.outcomes(lambda q: Prediction(refused=True) if not q.answerable else Prediction(rows=self.gold[q.id])))
        self.assertEqual(s["overall"]["rate"], 1.0)
        self.assertEqual(s["refusal_correctness"]["rate"], 1.0)
        self.assertEqual(s["false_refusal_rate"], 0.0)

    def test_an_agent_that_refuses_everything_is_right_only_on_the_unanswerable_questions(self):
        s = summarise(QS, self.outcomes(lambda q: Prediction(refused=True)))
        self.assertEqual(s["overall"]["correct"], 10)
        self.assertEqual(s["false_refusal_rate"], 1.0)

    def test_an_agent_that_never_refuses_fails_every_unanswerable_question(self):
        s = summarise(QS, self.outcomes(lambda q: Prediction(rows=self.gold.get(q.id, [(0,)]))))
        self.assertEqual(s["refusal_correctness"]["rate"], 0.0)
        self.assertEqual(s["outcome_counts"]["should_have_refused"], 10)

    def test_an_agent_that_ignores_the_definitions_scores_low_on_definition_questions(self):
        """Each question answered with its first trap switched off: the 'no definitions' failure mode."""
        s = summarise(QS, self.outcomes(lambda q: Prediction(refused=True) if not q.answerable
                                        else Prediction(rows=self.wrong.get(q.id, self.gold[q.id]))))
        self.assertEqual(s["by_kind"]["definition"]["correct"], 0)
        self.assertEqual(s["by_kind"]["control"]["rate"], 1.0)

    def test_missing_outcomes_cannot_pass_as_a_full_run(self):
        with self.assertRaises(ValueError):
            summarise(QS, {"d01": "correct"})
        with self.assertRaises(ValueError):
            summarise(QS, {q.id: "great" for q in QS})

    def test_no_result_and_false_refusal_are_distinct(self):
        q = next(x for x in QS if x.id == "d01")
        self.assertEqual(score(q, self.gold["d01"], None), "no_result")
        self.assertEqual(score(q, self.gold["d01"], Prediction()), "no_result")
        self.assertEqual(score(q, self.gold["d01"], Prediction(refused=True)), "false_refusal")
        self.assertEqual(score(q, self.gold["d01"], Prediction(rows=[(1,)])), "wrong_answer")


class TestRetrievalScoring(unittest.TestCase):
    def test_recall(self):
        self.assertEqual(recall(("a", "b"), ["a", "x"]), 0.5)
        self.assertIsNone(recall((), ["a"]))

    def test_perfect_and_empty_retrieval(self):
        by_text = {q.question: list(q.needs) for q in QS}
        perfect = evaluate_retrieval(QS, lambda text: by_text[text])
        self.assertEqual(perfect["mean_recall"], 1.0)
        self.assertEqual(perfect["complete"], 1.0)
        none = evaluate_retrieval(QS, lambda text: [])
        self.assertEqual(none["mean_recall"], 0.0)
        self.assertEqual(none["complete"], 0.0)
        self.assertEqual(none["questions"], perfect["questions"])

    def test_rule_and_metric_recall_are_scored_separately(self):
        only_rules = evaluate_retrieval(QS, lambda text: [c.id for c in chunks() if c.kind == "rule"])
        self.assertEqual(only_rules["rule_recall"], 1.0)
        self.assertEqual(only_rules["metric_recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
