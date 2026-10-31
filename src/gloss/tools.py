"""The four tools Gloss offers, as plain functions. The MCP server and the agent both use these."""
from __future__ import annotations

import datetime as dt
import decimal
from dataclasses import dataclass

import duckdb

from .dictionary import chunks, load_dictionary
from .retrieval import Retriever
from .warehouse import connect_readonly, run_query, table_names


@dataclass
class Warehouse:
    con: duckdb.DuckDBPyConnection
    tables: list[str]
    retriever: Retriever
    max_rows: int = 200
    timeout_s: float = 10.0

    @classmethod
    def open(cls, db_path: str, **kw) -> "Warehouse":
        con = connect_readonly(db_path)
        return cls(con, table_names(con), Retriever(chunks()), **kw)


def _json(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def list_tables(wh: Warehouse) -> list[dict]:
    tables = load_dictionary()["tables"]
    return [{"table": t, "description": " ".join(tables.get(t, {}).get("description", "").split())} for t in wh.tables]


def describe_table(wh: Warehouse, table: str) -> dict:
    name = table.strip().lower()
    if name not in wh.tables:
        raise KeyError(f"unknown table '{table}'; available tables: {', '.join(wh.tables)}")
    doc = load_dictionary()["tables"].get(name, {})
    cols = wh.con.execute("SELECT column_name, data_type FROM information_schema.columns "
                          "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position", [name]).fetchall()
    sample = run_query(wh.con, f"SELECT * FROM {name}", wh.tables, max_rows=3, timeout_s=wh.timeout_s)
    return {"table": name, "description": " ".join(doc.get("description", "").split()),
            "columns": [{"name": c, "type": t, "description": " ".join(str(doc.get("columns", {}).get(c, "")).split())} for c, t in cols],
            "sample_rows": [[_json(v) for v in row] for row in sample.rows]}


def search_definitions(wh: Warehouse, query: str, k: int = 3) -> list[dict]:
    k = max(1, min(int(k), 8))
    return [{"id": h.chunk.id, "title": h.chunk.title, "text": h.chunk.text, "score": round(h.score, 3)}
            for h in wh.retriever.search(query, k)]


def run_sql(wh: Warehouse, sql: str) -> dict:
    res = run_query(wh.con, sql, wh.tables, max_rows=wh.max_rows, timeout_s=wh.timeout_s)
    return {"columns": res.columns, "rows": [[_json(v) for v in row] for row in res.rows],
            "truncated": res.truncated, "elapsed_s": round(res.elapsed_s, 4)}
