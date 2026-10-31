"""Independent pure-Python versions of the metrics, used to check the reference SQL in dictionary.yaml.

Written from the English definitions, not from the SQL, and using no SQL at all.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

IST = timedelta(hours=5, minutes=30)
AS_OF = date(2026, 9, 30)


def ist_date(ts):
    return (ts + IST).date()


class Ref:
    def __init__(self, tables):
        self.customers = {c["customer_id"]: c for c in tables["customers"]}
        self.orders = tables["orders"]
        self.items_total = defaultdict(int)
        for i in tables["order_items"]:
            self.items_total[i["order_id"]] += i["quantity"] * i["unit_price"]
        self.refunded = defaultdict(int)
        for r in tables["refunds"]:
            self.refunded[r["order_id"]] += r["refund_amount"]

    def real(self, status=None):
        for o in self.orders:
            if self.customers[o["customer_id"]]["is_test"]:
                continue
            if status is None or o["status"] in status:
                yield o

    def value(self, o):
        return self.items_total[o["order_id"]] - o["discount_amount"]

    def gmv(self):
        return sum(self.items_total[o["order_id"]] for o in self.real() if o["status"] != "cancelled")

    def net_revenue(self, month=None):
        return sum(self.value(o) - self.refunded[o["order_id"]] for o in self.real({"completed"})
                   if month is None or (ist_date(o["ordered_at"]).year, ist_date(o["ordered_at"]).month) == month)

    def aov(self):
        done = list(self.real({"completed"}))
        return sum(self.value(o) for o in done) / len(done)

    def active_customers(self):
        lo = AS_OF - timedelta(days=89)
        return len({o["customer_id"] for o in self.real({"completed"}) if lo <= ist_date(o["ordered_at"]) <= AS_OF})

    def new_customers(self, year, month):
        first = {}
        for o in self.real({"completed"}):
            d = ist_date(o["ordered_at"])
            if o["customer_id"] not in first or d < first[o["customer_id"]]:
                first[o["customer_id"]] = d
        return sum(1 for d in first.values() if (d.year, d.month) == (year, month))

    def repeat_customers(self):
        n = defaultdict(int)
        for o in self.real({"completed"}):
            n[o["customer_id"]] += 1
        return sum(1 for v in n.values() if v >= 2)

    def cancellation_rate(self):
        rows = list(self.real({"completed", "cancelled", "returned"}))
        return sum(1 for o in rows if o["status"] == "cancelled") / len(rows)

    def return_rate(self):
        rows = list(self.real({"completed", "returned"}))
        return sum(1 for o in rows if o["status"] == "returned") / len(rows)

    def ltv(self):
        out = defaultdict(int)
        for o in self.real({"completed"}):
            out[o["customer_id"]] += self.value(o) - self.refunded[o["order_id"]]
        return dict(out)
