import asyncio
import unittest

from gloss.dictionary import chunks
from gloss.mcp_server import build_server
from gloss.retrieval import Retriever, tokenize
from gloss.tools import Warehouse, describe_table, list_tables, run_sql, search_definitions

from mcp.client import Client

from fixtures import warehouse


class TestRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Retriever(chunks())

    def top(self, q, k=3):
        return [h.chunk.id for h in self.r.search(q, k)]

    def test_tokenizer_splits_names_and_plurals(self):
        self.assertEqual(tokenize("net_revenue of customers"), ["net", "revenue", "customer"])

    def test_obvious_questions_find_their_metric(self):
        cases = {
            "What was our net revenue last month?": "metric:net_revenue",
            "How many active customers do we have?": "metric:active_customer",
            "What is the average order value?": "metric:aov",
            "What is the return rate?": "metric:return_rate",
            "What is the cancellation rate?": "metric:cancellation_rate",
            "How many repeat customers are there?": "metric:repeat_customer",
            "Show gross merchandise value": "metric:gmv",
        }
        for q, want in cases.items():
            self.assertIn(want, self.top(q), q)

    def test_timezone_and_test_account_rules_are_found(self):
        self.assertIn("rule:reporting_timezone", self.top("How many orders did we get each day in IST?"))
        self.assertIn("rule:test_accounts", self.top("exclude internal test accounts from the order count"))

    def test_nothing_relevant_returns_nothing(self):
        self.assertEqual(self.r.search("zzzz qqqq", 3), [])

    def test_ranking_is_deterministic(self):
        self.assertEqual(self.top("revenue"), self.top("revenue"))


class TestTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wh = Warehouse.open(warehouse()[0])

    @classmethod
    def tearDownClass(cls):
        cls.wh.con.close()

    def test_list_and_describe(self):
        self.assertEqual([t["table"] for t in list_tables(self.wh)], ["customers", "order_items", "orders", "refunds"])
        d = describe_table(self.wh, "Orders")
        self.assertEqual([c["name"] for c in d["columns"]][:3], ["order_id", "customer_id", "ordered_at"])
        self.assertTrue(all(c["description"] for c in d["columns"]))
        self.assertEqual(len(d["sample_rows"]), 3)
        with self.assertRaises(KeyError):
            describe_table(self.wh, "secrets")

    def test_search_limits_k(self):
        self.assertLessEqual(len(search_definitions(self.wh, "revenue", k=100)), 8)     # k is capped at 8
        self.assertEqual(len(search_definitions(self.wh, "revenue", k=0)), 1)           # and at least 1

    def test_run_sql_is_json_safe(self):
        import json
        out = run_sql(self.wh, "SELECT ordered_at, status FROM orders ORDER BY order_id LIMIT 2")
        json.dumps(out)
        self.assertEqual(len(out["rows"]), 2)


class TestMcpServer(unittest.TestCase):
    """Talk to the server through a real MCP client, in process."""

    @classmethod
    def setUpClass(cls):
        cls.wh = Warehouse.open(warehouse()[0])
        cls.server = build_server(cls.wh)

    @classmethod
    def tearDownClass(cls):
        cls.wh.con.close()

    def call(self, name, args):
        async def go():
            async with Client(self.server) as c:
                return await c.call_tool(name, args)
        return asyncio.run(go())

    def test_four_tools_are_listed(self):
        async def go():
            async with Client(self.server) as c:
                return [t.name for t in (await c.list_tools()).tools]
        self.assertEqual(sorted(asyncio.run(go())), ["describe_table", "list_tables", "run_sql", "search_definitions"])

    def test_a_good_query_returns_rows(self):
        r = self.call("run_sql", {"sql": "SELECT count(*) AS n FROM customers"})
        self.assertFalse(r.is_error)
        self.assertIn("2500", r.content[0].text)

    def test_unsafe_sql_is_an_error_the_model_can_read(self):
        for sql in ["DROP TABLE orders", "SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM secrets"]:
            r = self.call("run_sql", {"sql": sql})
            self.assertTrue(r.is_error, sql)
            self.assertTrue(r.content[0].text.strip(), sql)

    def test_database_errors_come_back_as_errors(self):
        r = self.call("run_sql", {"sql": "SELECT no_such_column FROM orders"})
        self.assertTrue(r.is_error)
        self.assertIn("no_such_column", r.content[0].text)

    def test_unknown_table_is_an_error(self):
        r = self.call("describe_table", {"table": "nope"})
        self.assertTrue(r.is_error)
        self.assertIn("available tables", r.content[0].text)


if __name__ == "__main__":
    unittest.main()
