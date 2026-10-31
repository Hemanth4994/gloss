"""Synthetic e-commerce data for Gloss. Fixed seed, so every run produces the same rows.

The data is made up. That is deliberate: because I wrote the generator, the true answer to every question is known exactly.
It also plants the traps real warehouses have, so a question can only be answered correctly with the right definition:

  * test accounts (is_test) that place large orders
  * orders that were cancelled, returned or are still pending
  * refunds kept in their own table (a returned order's refund must not be subtracted twice)
  * timestamps stored in UTC while the business reports in IST (UTC+05:30), so late-evening UTC orders belong to the next IST day
  * signup date is not the same as first purchase date

It also injects a known number of dirty rows (duplicates, orphans, bad values) so the loader's quality report can be checked
against what was really injected.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

SEED = 7
AS_OF = date(2026, 9, 30)                              # "today" for relative metrics, as an IST date
START = datetime(2025, 10, 1, 0, 0, 0)                 # UTC
END = datetime(2026, 9, 30, 18, 29, 59)                # last UTC instant that is still 30 Sep in IST
IST = timedelta(hours=5, minutes=30)

N_CUSTOMERS = 2500
CITIES = ["Bengaluru", "Hyderabad", "Mumbai", "Delhi", "Chennai", "Pune", "Kolkata", "Ahmedabad", "Jaipur", "Kochi"]
CHANNELS = ["organic", "paid_search", "social", "referral"]
CATEGORIES = {"grocery": (40, 400), "snacks": (20, 200), "beverages": (30, 300),
              "personal_care": (80, 700), "home": (150, 1500), "electronics": (500, 6000)}
# Relative order volume for each UTC hour. Note the real mass in 18-23 UTC, which is 23:30-05:30 IST.
HOUR_WEIGHTS = [3, 2, 2, 3, 5, 7, 8, 9, 8, 7, 6, 5, 4, 4, 4, 4, 4, 4, 5, 6, 6, 5, 4, 3]

# Counts of dirty rows injected on purpose (the loader must find exactly these).
INJECT = {"duplicate_customer_id": 12, "null_city": 20, "orphan_order": 15, "bad_item_value": 25,
          "duplicate_item_id": 10, "orphan_refund": 5, "refund_exceeds_order": 8}


@dataclass
class RawData:
    customers: list[dict]
    orders: list[dict]
    order_items: list[dict]
    refunds: list[dict]
    injected: dict[str, int] = field(default_factory=dict)


def _random_time(rng: random.Random, lo: datetime, hi: datetime) -> datetime:
    span_days = max((hi - lo).days, 1)
    day = lo + timedelta(days=rng.randrange(span_days))
    hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
    t = day.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60), microsecond=0)
    return min(max(t, lo), hi)


def generate_raw(seed: int = SEED) -> RawData:
    rng = random.Random(seed)

    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        signup = START + timedelta(days=rng.randrange(0, 330), hours=rng.randrange(24), minutes=rng.randrange(60))
        customers.append({"customer_id": cid, "signup_at": signup.replace(second=0), "city": rng.choice(CITIES),
                          "is_test": rng.random() < 0.03, "acquisition_channel": rng.choices(CHANNELS, [45, 25, 20, 10])[0]})

    drafts = []   # (ordered_at, customer, status, payment, discount_rate, items)
    for c in customers:
        n = rng.choices([0, 1, 2, 3, 4, 5, 6, 8, 10, 12], weights=[25, 25, 15, 10, 8, 6, 4, 3, 2, 2])[0]
        for _ in range(n):
            at = _random_time(rng, c["signup_at"], END)
            age_days = (END - at).days
            if age_days < 7:
                status = rng.choices(["pending", "completed", "cancelled"], [60, 35, 5])[0]
            else:
                status = rng.choices(["completed", "cancelled", "returned"], [86, 8, 6])[0]
            items = []
            for _ in range(rng.choices([1, 2, 3, 4], [45, 30, 15, 10])[0]):
                cat = rng.choice(list(CATEGORIES))
                lo, hi = CATEGORIES[cat]
                qty = rng.choices([1, 2, 3, 4], [60, 25, 10, 5])[0]
                if c["is_test"]:
                    qty = rng.randint(20, 100)                      # test accounts place huge orders
                items.append({"category": cat, "quantity": qty, "unit_price": rng.randint(lo, hi)})
            discount_rate = rng.choice([0, 0, 0, 0.05, 0.10, 0.15, 0.20])
            drafts.append((at, c["customer_id"], status, rng.choices(["upi", "card", "cod"], [60, 25, 15])[0],
                           discount_rate, items))
    drafts.sort(key=lambda d: (d[0], d[1]))

    orders, items_out, refunds = [], [], []
    item_id = refund_id = 0
    for order_id, (at, cid, status, pay, rate, items) in enumerate(drafts, start=1):
        total = sum(i["quantity"] * i["unit_price"] for i in items)
        discount = int(total * rate)
        orders.append({"order_id": order_id, "customer_id": cid, "ordered_at": at, "status": status,
                       "payment_method": pay, "discount_amount": discount})
        for i in items:
            item_id += 1
            items_out.append({"order_item_id": item_id, "order_id": order_id, **i})
        value = total - discount
        if status == "returned":
            refund_id += 1
            refunds.append({"refund_id": refund_id, "order_id": order_id, "refund_amount": value,
                            "refunded_at": min(at + timedelta(days=rng.randint(3, 9)), END)})
        elif status == "completed" and rng.random() < 0.04:
            refund_id += 1
            refunds.append({"refund_id": refund_id, "order_id": order_id,
                            "refund_amount": max(1, int(value * rng.uniform(0.10, 0.40))),
                            "refunded_at": min(at + timedelta(days=rng.randint(1, 7)), END)})

    injected = _inject_dirty_rows(rng, customers, orders, items_out, refunds)
    return RawData(customers, orders, items_out, refunds, injected)


def _inject_dirty_rows(rng, customers, orders, items, refunds) -> dict[str, int]:
    n = INJECT
    base_customers = list(customers)
    for c in rng.sample(base_customers, n["duplicate_customer_id"]):
        customers.append({**c, "city": "Duplicate"})                         # same id again, later in the file
    for c in rng.sample([c for c in base_customers if c["customer_id"] > 100], n["null_city"]):
        c["city"] = None

    orphan_items = 0
    next_order = max(o["order_id"] for o in orders)
    next_item = max(i["order_item_id"] for i in items)
    for _ in range(n["orphan_order"]):
        next_order += 1
        orders.append({"order_id": next_order, "customer_id": 900000 + next_order, "ordered_at": START + timedelta(days=rng.randrange(300)),
                       "status": "completed", "payment_method": "upi", "discount_amount": 0})
        for _ in range(rng.randint(1, 3)):
            next_item += 1
            items.append({"order_item_id": next_item, "order_id": next_order, "category": "grocery", "quantity": 1, "unit_price": 100})
            orphan_items += 1

    good_orders = [o for o in orders if o["order_id"] <= next_order - n["orphan_order"]]
    for o in rng.sample(good_orders, n["bad_item_value"]):                   # extra bad item rows on orders that keep valid items
        next_item += 1
        items.append({"order_item_id": next_item, "order_id": o["order_id"], "category": "snacks",
                      "quantity": rng.choice([0, -1, -3]), "unit_price": 50})
    for i in rng.sample([i for i in items if i["order_id"] <= next_order - n["orphan_order"]], n["duplicate_item_id"]):
        items.append(dict(i))                                                # exact repeat of an existing item row

    next_refund = max([r["refund_id"] for r in refunds] or [0])
    for _ in range(n["orphan_refund"]):
        next_refund += 1
        refunds.append({"refund_id": next_refund, "order_id": 800000 + next_refund, "refund_amount": 100, "refunded_at": START})
    totals = {}
    for i in items:
        if i["quantity"] > 0:
            totals[i["order_id"]] = totals.get(i["order_id"], 0) + i["quantity"] * i["unit_price"]
    refunded = {r["order_id"] for r in refunds}
    completed = [o for o in good_orders if o["status"] == "completed" and o["order_id"] not in refunded]
    for o in rng.sample(completed, n["refund_exceeds_order"]):
        next_refund += 1
        value = totals[o["order_id"]] - o["discount_amount"]
        refunds.append({"refund_id": next_refund, "order_id": o["order_id"], "refund_amount": value + 500, "refunded_at": END})

    return {**n, "orphan_order_items": orphan_items}
