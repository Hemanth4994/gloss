import os
import tempfile
import unittest

import duckdb

from gloss.warehouse import GuardError, QueryError, QueryTimeout, connect_readonly, run_query, table_names


class TestWarehouse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.dir.name, "t.duckdb")
        con = duckdb.connect(cls.path)
        con.execute("CREATE TABLE nums AS SELECT range AS n FROM range(100)")
        con.execute("CREATE TABLE big AS SELECT range AS n FROM range(30000)")
        con.close()
        cls.csv = os.path.join(cls.dir.name, "secret.csv")
        with open(cls.csv, "w") as f:
            f.write("a\n1\n")

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def setUp(self):
        self.con = connect_readonly(self.path)
        self.tables = table_names(self.con)

    def tearDown(self):
        self.con.close()

    def test_lists_tables(self):
        self.assertEqual(self.tables, ["big", "nums"])

    def test_runs_a_query(self):
        r = run_query(self.con, "SELECT sum(n) AS s FROM nums", self.tables)
        self.assertEqual((r.columns, r.rows, r.truncated), (["s"], [(4950,)], False))

    def test_row_cap_is_reported(self):
        r = run_query(self.con, "SELECT n FROM nums ORDER BY n", self.tables, max_rows=10)
        self.assertEqual(len(r.rows), 10)
        self.assertTrue(r.truncated)
        self.assertEqual(r.rows[0], (0,))      # order survives the cap

    def test_guard_runs_first(self):
        with self.assertRaises(GuardError):
            run_query(self.con, "DROP TABLE nums", self.tables)

    def test_database_error_is_readable(self):
        with self.assertRaises(QueryError) as cm:
            run_query(self.con, "SELECT missing_col FROM nums", self.tables)
        self.assertIn("missing_col", str(cm.exception))

    def test_timeout_stops_a_runaway_query(self):
        with self.assertRaises(QueryTimeout):
            run_query(self.con, "SELECT count(*) FROM big a, big b, big c", self.tables, timeout_s=0.5)

    def test_layer_two_blocks_writes_and_files_even_without_the_guard(self):
        with self.assertRaises(duckdb.Error):
            self.con.execute("CREATE TABLE x AS SELECT 1")
        with self.assertRaises(duckdb.Error):
            self.con.execute(f"SELECT * FROM read_csv('{self.csv}')").fetchall()
        with self.assertRaises(duckdb.Error):
            self.con.execute("SET enable_external_access = true")


if __name__ == "__main__":
    unittest.main()
