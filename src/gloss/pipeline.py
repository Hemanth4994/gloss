"""Load the raw rows into DuckDB, rejecting bad rows and writing a data-quality report.

    python -m gloss.pipeline --out data/gloss.duckdb --report data/quality_report.json

Rules, in the order they are applied. A row is counted once, under the first rule it breaks.
  customers : duplicate customer_id (keep the first)            -> rejected "duplicate_customer_id"
              missing city                                      -> kept, city = 'Unknown', counted as repaired "null_city"
  orders    : customer_id not in customers                      -> rejected "orphan_order"
  items     : duplicate order_item_id (keep the first)          -> rejected "duplicate_item_id"
              order missing or rejected                         -> rejected "item_of_rejected_order"
              quantity or unit_price not positive               -> rejected "bad_item_value"
  orders    : no valid item left                                -> rejected "order_without_items"
  refunds   : order missing or rejected                         -> rejected "orphan_refund"
              amount not positive                               -> rejected "bad_refund_amount"
              amount larger than the order's value              -> rejected "refund_exceeds_order"
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass, field

import duckdb

from .datagen import SEED, RawData, generate_raw

SCHEMA = {
    "customers": "customer_id INTEGER PRIMARY KEY, signup_at TIMESTAMP, city VARCHAR, is_test BOOLEAN, acquisition_channel VARCHAR",
    "orders": "order_id INTEGER PRIMARY KEY, customer_id INTEGER, ordered_at TIMESTAMP, status VARCHAR, payment_method VARCHAR, discount_amount INTEGER",
    "order_items": "order_item_id INTEGER PRIMARY KEY, order_id INTEGER, category VARCHAR, quantity INTEGER, unit_price INTEGER",
    "refunds": "refund_id INTEGER PRIMARY KEY, order_id INTEGER, refund_amount INTEGER, refunded_at TIMESTAMP",
}
COLUMNS = {t: [c.split()[0] for c in cols.split(", ")] for t, cols in SCHEMA.items()}


@dataclass
class QualityReport:
    rows_in: dict[str, int] = field(default_factory=dict)
    rows_out: dict[str, int] = field(default_factory=dict)
    rejected: Counter = field(default_factory=Counter)
    repaired: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        return {"rows_in": self.rows_in, "rows_out": self.rows_out,
                "rejected": dict(sorted(self.rejected.items())), "repaired": dict(sorted(self.repaired.items()))}


def clean(raw: RawData) -> tuple[dict[str, list[dict]], QualityReport]:
    rep = QualityReport(rows_in={"customers": len(raw.customers), "orders": len(raw.orders),
                                 "order_items": len(raw.order_items), "refunds": len(raw.refunds)})
    customers, seen = [], set()
    for c in raw.customers:
        if c["customer_id"] in seen:
            rep.rejected["duplicate_customer_id"] += 1
            continue
        seen.add(c["customer_id"])
        if c["city"] is None:
            c = {**c, "city": "Unknown"}
            rep.repaired["null_city"] += 1
        customers.append(c)

    orders = []
    for o in raw.orders:
        if o["customer_id"] not in seen:
            rep.rejected["orphan_order"] += 1
        else:
            orders.append(o)
    order_ids = {o["order_id"] for o in orders}

    items, seen_items = [], set()
    for i in raw.order_items:
        if i["order_item_id"] in seen_items:
            rep.rejected["duplicate_item_id"] += 1
        elif i["order_id"] not in order_ids:
            rep.rejected["item_of_rejected_order"] += 1
        elif i["quantity"] <= 0 or i["unit_price"] <= 0:
            rep.rejected["bad_item_value"] += 1
        else:
            items.append(i)
        seen_items.add(i["order_item_id"])

    totals: dict[int, int] = {}
    for i in items:
        totals[i["order_id"]] = totals.get(i["order_id"], 0) + i["quantity"] * i["unit_price"]
    kept_orders = []
    for o in orders:
        if o["order_id"] in totals:
            kept_orders.append(o)
        else:
            rep.rejected["order_without_items"] += 1
    orders = kept_orders
    value = {o["order_id"]: totals[o["order_id"]] - o["discount_amount"] for o in orders}

    refunds = []
    for r in raw.refunds:
        if r["order_id"] not in value:
            rep.rejected["orphan_refund"] += 1
        elif r["refund_amount"] <= 0:
            rep.rejected["bad_refund_amount"] += 1
        elif r["refund_amount"] > value[r["order_id"]]:
            rep.rejected["refund_exceeds_order"] += 1
        else:
            refunds.append(r)

    tables = {"customers": customers, "orders": orders, "order_items": items, "refunds": refunds}
    rep.rows_out = {t: len(rows) for t, rows in tables.items()}
    return tables, rep


def _insert_many(con, name: str, cols: list[str], rows: list[dict], batch: int = 400) -> None:
    """Multi-row INSERT in batches: far faster than one executemany row at a time."""
    one = "(" + ", ".join("?" for _ in cols) + ")"
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        flat = [row[c] for row in chunk for c in cols]
        con.execute(f"INSERT INTO {name} VALUES " + ", ".join([one] * len(chunk)), flat)


def write_duckdb(tables: dict[str, list[dict]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    con = duckdb.connect(path)
    try:
        for name, ddl in SCHEMA.items():
            con.execute(f"CREATE TABLE {name} ({ddl})")
            _insert_many(con, name, COLUMNS[name], tables[name])
    finally:
        con.close()


def build_warehouse(path: str, seed: int = SEED) -> tuple[dict[str, list[dict]], QualityReport, RawData]:
    raw = generate_raw(seed)
    tables, report = clean(raw)
    write_duckdb(tables, path)
    return tables, report, raw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/gloss.duckdb")
    ap.add_argument("--report", default="data/quality_report.json")
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    _, report, _ = build_warehouse(a.out, a.seed)
    os.makedirs(os.path.dirname(a.report) or ".", exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(json.dumps(report.to_dict(), indent=2))
    print(f"warehouse -> {a.out}")


if __name__ == "__main__":
    main()
