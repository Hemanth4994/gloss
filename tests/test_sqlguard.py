import unittest

from gloss.sqlguard import check_sql

TABLES = ["orders", "customers", "order_items"]


def ok(sql):
    return check_sql(sql, TABLES).ok


class TestAllowed(unittest.TestCase):
    def test_plain_select(self):
        self.assertTrue(ok("SELECT count(*) FROM orders"))

    def test_join_and_aggregate_with_trailing_semicolon_and_comment(self):
        self.assertTrue(ok("SELECT c.id, sum(o.amount) FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY 1; -- done"))

    def test_trailing_semicolons_and_comments(self):
        for sql in ["SELECT 1 FROM orders;", "SELECT 1 FROM orders; -- done", "SELECT 1 FROM orders;\n-- trailing\n",
                    "/* note */ SELECT 1 FROM orders;;"]:
            self.assertTrue(ok(sql), sql)

    def test_a_comment_cannot_hide_a_second_statement(self):
        self.assertFalse(ok("SELECT 1 FROM orders; -- x\nDROP TABLE orders"))
        self.assertFalse(ok("SELECT 1 FROM orders; /* x */ DELETE FROM orders"))

    def test_cte_union_window(self):
        self.assertTrue(ok("WITH a AS (SELECT * FROM orders) SELECT * FROM a UNION ALL SELECT * FROM a"))
        self.assertTrue(ok("SELECT id, row_number() OVER (PARTITION BY customer_id ORDER BY ordered_at) FROM orders"))

    def test_subquery_and_case_insensitive_names(self):
        self.assertTrue(ok("SELECT * FROM (SELECT * FROM ORDERS) x"))
        self.assertTrue(ok("SELECT * FROM main.orders"))

    def test_common_functions(self):
        self.assertTrue(ok("SELECT date_trunc('month', ordered_at), median(amount), strftime(ordered_at, '%Y') FROM orders GROUP BY 1, 3"))


class TestRefused(unittest.TestCase):
    def assertRefused(self, sql, word=None):
        v = check_sql(sql, TABLES)
        self.assertFalse(v.ok, sql)
        if word:
            self.assertIn(word, v.reason.lower())

    def test_other_statements(self):
        for sql in ["DROP TABLE orders", "DELETE FROM orders", "INSERT INTO orders VALUES (1)",
                    "UPDATE orders SET amount = 0", "CREATE TABLE t AS SELECT 1", "ALTER TABLE orders ADD COLUMN x INT",
                    "COPY orders TO 'out.csv'", "ATTACH 'x.db'", "PRAGMA database_list", "SET threads = 1"]:
            self.assertRefused(sql)

    def test_two_statements(self):
        self.assertRefused("SELECT 1 FROM orders; DROP TABLE orders", "one statement")

    def test_table_functions_and_paths(self):
        self.assertRefused("SELECT * FROM read_csv('/etc/passwd')", "named tables")
        self.assertRefused("SELECT * FROM range(5)", "named tables")
        self.assertRefused("SELECT * FROM 'data.csv'")      # a quoted path is read as an unknown table name

    def test_unknown_and_foreign_tables(self):
        self.assertRefused("SELECT * FROM secrets", "not available")
        self.assertRefused("SELECT * FROM information_schema.tables")
        self.assertRefused("SELECT * FROM other.orders", "schema")

    def test_denied_functions(self):
        self.assertRefused("SELECT getenv('HOME') FROM orders", "getenv")
        self.assertRefused("SELECT current_setting('threads') FROM orders")
        self.assertRefused("SELECT read_text('/etc/passwd') FROM orders")

    def test_garbage_and_empty(self):
        self.assertRefused("")
        self.assertRefused("   ")
        self.assertRefused("-- only a comment")
        self.assertRefused("SELEKT * FROM orders")

    def test_reason_lists_available_tables(self):
        v = check_sql("SELECT * FROM nope", TABLES)
        self.assertIn("customers", v.reason)


if __name__ == "__main__":
    unittest.main()
