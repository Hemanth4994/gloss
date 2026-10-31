import unittest
from collections import Counter

from gloss.datagen import generate_raw
from gloss.pipeline import clean
from gloss.warehouse import connect_readonly, table_names

from fixtures import warehouse


class TestGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = generate_raw()

    def test_same_seed_same_data(self):
        again = generate_raw()
        self.assertEqual(self.raw.orders, again.orders)
        self.assertEqual(self.raw.refunds, again.refunds)
        self.assertNotEqual(self.raw.orders, generate_raw(seed=8).orders)

    def test_traps_are_present(self):
        statuses = Counter(o["status"] for o in self.raw.orders)
        for s in ("completed", "cancelled", "returned", "pending"):
            self.assertGreater(statuses[s], 50, s)
        tests = [c for c in self.raw.customers if c["is_test"]]
        self.assertTrue(0.01 < len({c["customer_id"] for c in tests}) / 2500 < 0.06)
        test_ids = {c["customer_id"] for c in tests}
        self.assertTrue(any(o["customer_id"] in test_ids for o in self.raw.orders))
        late = sum(1 for o in self.raw.orders
                   if (o["ordered_at"].hour, o["ordered_at"].minute) >= (18, 30))
        self.assertGreater(late / len(self.raw.orders), 0.10)           # enough orders whose IST date differs from the UTC date
        self.assertGreater(Counter(o["status"] for o in self.raw.orders)["pending"], 0)

    def test_no_order_is_after_the_as_of_day(self):
        self.assertLessEqual(max(o["ordered_at"] for o in self.raw.orders), __import__("gloss.datagen").datagen.END)


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.tables, cls.report, cls.raw = warehouse()

    def test_report_matches_exactly_what_was_injected(self):
        inj = self.raw.injected
        r = self.report
        self.assertEqual(r.rejected["duplicate_customer_id"], inj["duplicate_customer_id"])
        self.assertEqual(r.repaired["null_city"], inj["null_city"])
        self.assertEqual(r.rejected["orphan_order"], inj["orphan_order"])
        self.assertEqual(r.rejected["item_of_rejected_order"], inj["orphan_order_items"])
        self.assertEqual(r.rejected["bad_item_value"], inj["bad_item_value"])
        self.assertEqual(r.rejected["duplicate_item_id"], inj["duplicate_item_id"])
        self.assertEqual(r.rejected["orphan_refund"], inj["orphan_refund"])
        self.assertEqual(r.rejected["refund_exceeds_order"], inj["refund_exceeds_order"])
        self.assertEqual(sum(r.rejected.values()),
                         sum(inj[k] for k in ("duplicate_customer_id", "orphan_order", "bad_item_value", "duplicate_item_id",
                                              "orphan_refund", "refund_exceeds_order")) + inj["orphan_order_items"])
        self.assertEqual(r.rejected["order_without_items"], 0)          # nothing else was wrong

    def test_database_matches_the_clean_rows_and_is_consistent(self):
        con = connect_readonly(self.path)
        try:
            self.assertEqual(table_names(con), ["customers", "order_items", "orders", "refunds"])
            for name, rows in self.tables.items():
                self.assertEqual(con.execute(f"SELECT count(*) FROM {name}").fetchone()[0], len(rows), name)
            checks = {
                "orders without a customer": "SELECT count(*) FROM orders o LEFT JOIN customers c USING (customer_id) WHERE c.customer_id IS NULL",
                "items without an order": "SELECT count(*) FROM order_items i LEFT JOIN orders o USING (order_id) WHERE o.order_id IS NULL",
                "refunds without an order": "SELECT count(*) FROM refunds r LEFT JOIN orders o USING (order_id) WHERE o.order_id IS NULL",
                "non-positive items": "SELECT count(*) FROM order_items WHERE quantity <= 0 OR unit_price <= 0",
                "null cities": "SELECT count(*) FROM customers WHERE city IS NULL",
            }
            for label, sql in checks.items():
                self.assertEqual(con.execute(sql).fetchone()[0], 0, label)
        finally:
            con.close()

    def test_every_refund_fits_its_order(self):
        con = connect_readonly(self.path)
        try:
            bad = con.execute("""
                WITH t AS (SELECT order_id, SUM(quantity*unit_price) AS items_total FROM order_items GROUP BY 1)
                SELECT count(*) FROM refunds r JOIN orders o USING (order_id) JOIN t USING (order_id)
                WHERE r.refund_amount > t.items_total - o.discount_amount""").fetchone()[0]
            self.assertEqual(bad, 0)
        finally:
            con.close()

    def test_cleaning_is_repeatable(self):
        tables2, report2 = clean(generate_raw())
        self.assertEqual(report2.to_dict(), self.report.to_dict())


if __name__ == "__main__":
    unittest.main()
