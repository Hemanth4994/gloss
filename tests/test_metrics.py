"""The reference SQL in dictionary.yaml must agree with an independent pure-Python calculation,
and each planted trap must really change the answer (otherwise it would not test anything)."""
import unittest

from gloss.dictionary import chunks, load_dictionary, reference_sql
from gloss.warehouse import connect_readonly, run_query, table_names

from fixtures import warehouse
from pyref import Ref


class TestMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path, tables, _, _ = warehouse()
        cls.ref = Ref(tables)
        cls.con = connect_readonly(path)
        cls.tables = table_names(cls.con)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def one(self, sql):
        return run_query(self.con, sql, self.tables, max_rows=10).rows[0][0]

    def test_gmv(self):
        self.assertEqual(self.one(reference_sql("gmv")), self.ref.gmv())

    def test_net_revenue(self):
        self.assertEqual(self.one(reference_sql("net_revenue")), self.ref.net_revenue())

    def test_aov(self):
        self.assertAlmostEqual(float(self.one(reference_sql("aov"))), self.ref.aov(), places=6)

    def test_active_customers(self):
        self.assertEqual(self.one(reference_sql("active_customer")), self.ref.active_customers())

    def test_new_customers_in_august(self):
        self.assertEqual(self.one(reference_sql("new_customer")), self.ref.new_customers(2026, 8))

    def test_repeat_customers(self):
        self.assertEqual(self.one(reference_sql("repeat_customer")), self.ref.repeat_customers())

    def test_rates(self):
        self.assertAlmostEqual(float(self.one(reference_sql("cancellation_rate"))), self.ref.cancellation_rate(), places=9)
        self.assertAlmostEqual(float(self.one(reference_sql("return_rate"))), self.ref.return_rate(), places=9)

    def test_customer_ltv(self):
        res = run_query(self.con, reference_sql("customer_ltv"), self.tables, max_rows=100000)
        self.assertFalse(res.truncated)
        self.assertEqual({cid: int(v) for cid, v in res.rows}, self.ref.ltv())

    def test_every_metric_has_reference_sql_that_runs(self):
        for m in load_dictionary()["metrics"]:
            res = run_query(self.con, reference_sql(m["name"]), self.tables, max_rows=5)
            self.assertTrue(res.rows, m["name"])

    def test_chunks_never_leak_reference_sql(self):
        for c in chunks():
            self.assertNotIn("SELECT", c.text.upper(), c.id)
        self.assertEqual(len({c.id for c in chunks()}), len(chunks()))


class TestTrapsChangeTheAnswer(unittest.TestCase):
    """A trap that does not change the number is not a trap."""

    setUpClass = classmethod(TestMetrics.setUpClass.__func__)
    tearDownClass = classmethod(TestMetrics.tearDownClass.__func__)
    one = TestMetrics.one

    def test_forgetting_test_accounts(self):
        wrong = self.one("""WITH t AS (SELECT order_id, SUM(quantity*unit_price) AS items_total FROM order_items GROUP BY 1)
            SELECT SUM(t.items_total - o.discount_amount) FROM orders o JOIN t USING (order_id) WHERE o.status='completed'""")
        self.assertNotEqual(wrong, self.ref.net_revenue())
        self.assertGreater(wrong, self.ref.net_revenue() * 1.05)       # test accounts are not a rounding error

    def test_joining_items_and_refunds_directly_multiplies_rows(self):
        wrong = self.one("""SELECT SUM(i.quantity*i.unit_price - o.discount_amount - COALESCE(r.refund_amount,0))
            FROM orders o JOIN customers c USING (customer_id) JOIN order_items i USING (order_id)
            LEFT JOIN refunds r USING (order_id) WHERE o.status='completed' AND NOT c.is_test""")
        self.assertNotEqual(wrong, self.ref.net_revenue())

    def test_subtracting_every_refund_double_counts_returned_orders(self):
        # Returned orders are already excluded, so their (full) refunds must not be subtracted again.
        wrong = self.one("""WITH t AS (SELECT order_id, SUM(quantity*unit_price) AS items_total FROM order_items GROUP BY 1)
            SELECT SUM(t.items_total - o.discount_amount)
                   - (SELECT SUM(r.refund_amount) FROM refunds r JOIN orders o2 USING (order_id)
                      JOIN customers c2 ON c2.customer_id = o2.customer_id WHERE NOT c2.is_test)
            FROM orders o JOIN customers c USING (customer_id) JOIN t USING (order_id)
            WHERE o.status='completed' AND NOT c.is_test""")
        self.assertLess(wrong, self.ref.net_revenue())

    def test_including_returned_orders_without_their_refunds(self):
        wrong = self.one("""WITH t AS (SELECT order_id, SUM(quantity*unit_price) AS items_total FROM order_items GROUP BY 1)
            SELECT SUM(t.items_total - o.discount_amount)
            FROM orders o JOIN customers c USING (customer_id) JOIN t USING (order_id)
            WHERE o.status IN ('completed','returned') AND NOT c.is_test""")
        self.assertGreater(wrong, self.ref.net_revenue())

    def test_using_utc_dates_instead_of_ist_moves_orders_between_months(self):
        rows = run_query(self.con, """SELECT date_trunc('month', ordered_at) AS m_utc, date_trunc('month', ordered_at + INTERVAL 330 MINUTE) AS m_ist
            FROM orders WHERE m_utc <> m_ist""", self.tables, max_rows=5).rows
        self.assertTrue(rows)                                             # some orders really change month

    def test_signup_month_is_not_first_purchase_month(self):
        wrong = self.one("""SELECT COUNT(*) FROM customers WHERE NOT is_test
            AND signup_at >= TIMESTAMP '2026-08-01' AND signup_at < TIMESTAMP '2026-09-01'""")
        self.assertNotEqual(wrong, self.ref.new_customers(2026, 8))

    def test_pending_orders_change_the_rates(self):
        wrong = self.one("""SELECT SUM(CASE WHEN o.status='cancelled' THEN 1 ELSE 0 END)*1.0/COUNT(*)
            FROM orders o JOIN customers c USING (customer_id) WHERE NOT c.is_test""")
        self.assertNotAlmostEqual(float(wrong), self.ref.cancellation_rate(), places=4)


if __name__ == "__main__":
    unittest.main()
