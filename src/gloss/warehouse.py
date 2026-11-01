"""Run model-written SQL safely against a DuckDB file.

Layer 1: sqlguard.check_sql (static checks).
Layer 2: the connection is read-only, has no file or network access, and its configuration is locked.
On top of that: a row cap enforced inside the engine and a wall-clock timeout that interrupts the query.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import duckdb

from .sqlguard import check_sql


class GuardError(ValueError):
    """The query was refused before running. The message is written for the agent to read."""


class QueryTimeout(TimeoutError):
    """The query ran longer than the allowed time and was interrupted."""


class QueryError(RuntimeError):
    """The database rejected the query (wrong column, type error, ...). The message helps the agent fix it."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_s: float


def connect_readonly(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path, read_only=True, config={"enable_external_access": False})
    try:
        con.execute("SET lock_configuration = true")
    except duckdb.InvalidInputException as exc:
        # Connections to the same file in one process share one database instance. A second connection finds it already locked,
        # which is the state we want, so that is fine. Anything else is a real error.
        if "locked" not in str(exc):
            raise
    return con


def table_names(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def run_query(con: duckdb.DuckDBPyConnection, sql: str, allowed_tables: list[str],
              max_rows: int = 200, timeout_s: float = 10.0) -> QueryResult:
    verdict = check_sql(sql, allowed_tables)
    if not verdict.ok:
        raise GuardError(verdict.reason)

    body = sql.strip().rstrip(";")
    limited = f"SELECT * FROM ({body}) AS _q LIMIT {int(max_rows) + 1}"   # the cap is applied by the engine
    cur = con.cursor()
    fired = threading.Event()

    def _interrupt() -> None:
        fired.set()
        cur.interrupt()

    timer = threading.Timer(timeout_s, _interrupt)
    start = time.perf_counter()
    timer.start()
    try:
        cur.execute(limited)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except duckdb.Error as exc:
        if fired.is_set():
            raise QueryTimeout(f"query exceeded {timeout_s:g}s and was stopped") from exc
        raise QueryError(str(exc).splitlines()[0][:300]) from exc
    finally:
        timer.cancel()
    truncated = len(rows) > max_rows
    return QueryResult(columns, rows[:max_rows], truncated, time.perf_counter() - start)
