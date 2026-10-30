"""Static checks for model-written SQL.

This is the first of two layers. The second is the connection itself, which is read-only and has no
file or network access (see warehouse.py). Either layer alone should stop a bad query; having both
means one mistake in one layer is not enough.

Allowed: one SELECT-style query (WITH, UNION, subqueries, window functions) over named tables from an
allow-list. Not allowed: any other statement, table functions, file paths used as tables, other schemas,
and a short list of functions that read files, environment or settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_FORBIDDEN_NODES = tuple(
    getattr(exp, n)
    for n in ("Insert", "Update", "Delete", "Create", "Drop", "Alter", "Merge", "Copy", "Command",
              "Pragma", "Set", "Attach", "Use", "Transaction", "Install")
    if hasattr(exp, n)
)
_DENY_FUNCS = {"glob", "getenv", "current_setting", "query", "query_table"}
_DENY_PREFIXES = ("read_", "duckdb_", "pragma_", "parquet_", "sniff_", "iceberg_", "delta_")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def check_sql(sql: str, allowed_tables: Iterable[str]) -> Verdict:
    """Return Verdict(ok=True) if the query may run, otherwise ok=False with a reason the agent can read."""
    allowed = {t.lower() for t in allowed_tables}
    if not sql or not sql.strip():
        return Verdict(False, "empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except SqlglotError as exc:
        return Verdict(False, f"could not parse the SQL: {str(exc).splitlines()[0][:200]}")
    # sqlglot reports a trailing ';' (and a comment after it) as an extra empty "Semicolon" statement. Ignore those.
    statements = [s for s in statements if not isinstance(s, exp.Semicolon)]
    if not statements:
        return Verdict(False, "no statement found")
    if len(statements) > 1:
        return Verdict(False, "only one statement is allowed")

    tree = statements[0]
    if not isinstance(tree, exp.Query):
        return Verdict(False, f"only SELECT queries are allowed (got {type(tree).__name__.upper()})")
    if tree.find(*_FORBIDDEN_NODES):
        return Verdict(False, "the query contains a statement that changes data or settings")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return Verdict(False, "only named tables are allowed (no table functions or file paths)")
        if table.catalog or (table.db and table.db.lower() != "main"):
            return Verdict(False, f"table '{table.sql()}' is outside the allowed schema")
        name = table.name.lower()
        if name not in ctes and name not in allowed:
            return Verdict(False, f"table '{table.name}' is not available; available tables: {', '.join(sorted(allowed))}")

    for fn in tree.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in _DENY_FUNCS or name.startswith(_DENY_PREFIXES):
            return Verdict(False, f"function '{name}' is not allowed")
    return Verdict(True)
