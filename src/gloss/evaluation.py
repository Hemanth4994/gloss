"""The Gloss evaluation set: questions with gold SQL, a result matcher, an outcome scorer and a retrieval scorer.

Everything here is deterministic and needs no model. The agent is scored by comparing the RESULT of the SQL it ran with the
result of the gold SQL ("execution accuracy"), never by comparing SQL text, and never by reading its prose.

Gold SQL is written with macros (see MACROS) so that each trap can be switched off to produce a plausible WRONG query. The tests
use that to prove every question really is sensitive to the definition it is meant to test:

    test    forget to exclude test accounts
    tz      use the UTC date instead of the IST date
    status  ignore the order-status rule (count every status)
    refund  forget that refunds are subtracted
"""
from __future__ import annotations

import datetime as dt
import decimal
import itertools
import os
import re
from dataclasses import dataclass

import yaml

from .dictionary import chunks
from .warehouse import QueryResult, run_query

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
QUESTIONS_PATH = os.path.join(ROOT, "eval", "questions.yaml")
GOLD_PATH = os.path.join(ROOT, "eval", "gold_answers.json")

SPLITS = ("dev", "test")
KINDS = ("definition", "join", "control", "unanswerable")
MUTATIONS = ("test", "tz", "status", "refund")

# name -> (correct text, {mutation: wrong text}). Macros may use other macros; IST_RANGE takes two ISO dates.
MACROS: dict[str, tuple[str, dict[str, str]]] = {
    "IST_DATE": ("CAST(o.ordered_at + INTERVAL 330 MINUTE AS DATE)", {"tz": "CAST(o.ordered_at AS DATE)"}),
    "SIGNUP_DATE": ("CAST(c.signup_at + INTERVAL 330 MINUTE AS DATE)", {"tz": "CAST(c.signup_at AS DATE)"}),
    "REAL": ("NOT c.is_test", {"test": "TRUE"}),
    "COMPLETED": ("o.status = 'completed'", {"status": "TRUE"}),
    "NOT_CANCELLED": ("o.status <> 'cancelled'", {"status": "TRUE"}),
    "DECIDED": ("o.status IN ('completed', 'cancelled', 'returned')", {"status": "TRUE"}),     # cancellation-rate denominator
    "DELIVERED": ("o.status IN ('completed', 'returned')", {"status": "o.status <> 'cancelled'"}),   # return-rate denominator
    "REFUNDED": ("COALESCE(r.refunded, 0)", {"refund": "0"}),
    "ITEM_TOTALS": ("item_totals AS (SELECT order_id, SUM(quantity * unit_price) AS items_total FROM order_items GROUP BY order_id)", {}),
    "REFUND_TOTALS": ("refund_totals AS (SELECT order_id, SUM(refund_amount) AS refunded FROM refunds GROUP BY order_id)", {}),
    "NET_VALUE": ("(i.items_total - o.discount_amount - {REFUNDED})", {}),
    "ORDERS_C": ("FROM orders o JOIN customers c ON c.customer_id = o.customer_id", {}),
    "ITEMS_FROM": ("{ORDERS_C} JOIN item_totals i ON i.order_id = o.order_id", {}),
    "NET_FROM": ("{ITEMS_FROM} LEFT JOIN refund_totals r ON r.order_id = o.order_id", {}),
    "GMV_FROM": ("FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id", {}),
    "FIRST_ORDERS": ("first_orders AS (SELECT o.customer_id, c.acquisition_channel, {SIGNUP_DATE} AS signup_day, "
                     "MIN({IST_DATE}) AS first_day {ORDERS_C} WHERE {COMPLETED} AND {REAL} "
                     "GROUP BY o.customer_id, c.acquisition_channel, c.signup_at)", {}),
}
_MACRO = re.compile(r"\{([A-Z_]+)((?: [^{}]*)?)\}")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MIDNIGHT = re.compile(r"(\d{4}-\d{2}-\d{2})[T ]00:00:00(?:\.0+)?")


def expand(sql: str, mutation: str | None = None) -> str:
    """Expand macros. With a mutation, the macros that mutation targets produce the wrong text instead of the right one."""
    if mutation is not None and mutation not in MUTATIONS:
        raise ValueError(f"unknown mutation {mutation!r}")

    def sub(m: re.Match) -> str:
        name, args = m.group(1), m.group(2).split()
        if name == "IST_RANGE":
            if len(args) != 2 or not all(_ISO.match(a) for a in args):
                raise ValueError(f"IST_RANGE needs two ISO dates, got {args}")
            return f"{{IST_DATE}} BETWEEN DATE '{args[0]}' AND DATE '{args[1]}'"
        if name not in MACROS:
            raise KeyError(f"unknown macro {{{name}}}")
        text, wrong = MACROS[name]
        return wrong.get(mutation, text) if mutation else text

    for _ in range(8):
        new = _MACRO.sub(sub, sql)
        if new == sql:
            break
        sql = new
    else:
        raise ValueError("macro expansion did not finish (a macro refers to itself?)")
    if "{" in sql or "}" in sql:
        raise ValueError("unexpanded braces left in SQL")
    return " ".join(sql.split())


@dataclass(frozen=True)
class Question:
    id: str
    split: str
    kind: str
    question: str
    gold: str = ""                       # macro SQL; empty for unanswerable questions
    needs: tuple[str, ...] = ()          # chunk ids a correct answer depends on (rules and metrics)
    traps: tuple[str, ...] = ()          # mutations that must change the gold result
    anti: str = ""                       # controls: a query that wrongly applies a rule, which must NOT match the gold result
    ordered: bool = False                # row order matters (top-N questions)
    places: int = 2                      # decimals compared for non-integer numbers
    why: str = ""                        # unanswerable: what is missing from the data
    missing: tuple[str, ...] = ()        # unanswerable: words that must not appear in any table or column name

    @property
    def answerable(self) -> bool:
        return self.kind != "unanswerable"


def load_questions(path: str = QUESTIONS_PATH) -> list[Question]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    chunk_ids = {c.id for c in chunks()}
    out: list[Question] = []
    seen: set[str] = set()
    for r in raw:
        q = Question(id=r["id"], split=r["split"], kind=r["kind"], question=" ".join(r["question"].split()),
                     gold=(r.get("gold") or "").strip(), needs=tuple(r.get("needs", ())), traps=tuple(r.get("traps", ())),
                     anti=(r.get("anti") or "").strip(), ordered=bool(r.get("ordered", False)),
                     places=int(r.get("places", 2)), why=" ".join(str(r.get("why", "")).split()),
                     missing=tuple(r.get("missing", ())))
        if q.id in seen:
            raise ValueError(f"duplicate question id {q.id}")
        seen.add(q.id)
        if q.split not in SPLITS or q.kind not in KINDS:
            raise ValueError(f"{q.id}: bad split or kind")
        if q.answerable and not q.gold:
            raise ValueError(f"{q.id}: answerable question without gold SQL")
        if not q.answerable and (q.gold or q.needs or q.traps or not q.why or not q.missing):
            raise ValueError(f"{q.id}: an unanswerable question has no SQL or needs, but needs why and missing")
        unknown = [n for n in q.needs if n not in chunk_ids]
        if unknown:
            raise ValueError(f"{q.id}: needs unknown chunks {unknown}")
        if any(t not in MUTATIONS for t in q.traps):
            raise ValueError(f"{q.id}: unknown trap in {q.traps}")
        out.append(q)
    return out


def split_of(questions: list[Question], split: str) -> list[Question]:
    return [q for q in questions if q.split == split]


# ---------------------------------------------------------------- gold answers

def run_gold(con, tables: list[str], q: Question, mutation: str | None = None, sql: str | None = None) -> QueryResult:
    """Run a question's gold SQL (or a mutated or anti version) through the SAME guarded runner the agent uses."""
    text = expand(sql if sql is not None else q.gold, mutation)
    return run_query(con, text, tables, max_rows=1000, timeout_s=30.0)


def snapshot(res: QueryResult) -> dict:
    """The stable JSON form of a gold result, used for eval/gold_answers.json."""
    return {"columns": res.columns, "rows": [[_norm(v) for v in row] for row in res.rows]}


# ---------------------------------------------------------------- matching

def _norm(v):
    """One cell in a form that compares and serialises the same way everywhere (ints stay ints, other numbers become floats)."""
    if isinstance(v, str):                      # the agent's rows arrive as JSON, so a midnight timestamp is a string
        m = _MIDNIGHT.fullmatch(v)
        return m.group(1) if m else v
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, (float, decimal.Decimal)):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _same(g, p, places: int) -> bool:
    if isinstance(g, bool) or isinstance(p, bool):
        return isinstance(g, bool) and isinstance(p, bool) and g == p
    if _is_num(g) and _is_num(p):
        if isinstance(g, int):                     # counts and rupee totals must match exactly
            return abs(p - g) < 1e-9
        return abs(p - g) <= 0.5 * 10 ** (-places) + 1e-9
    return g == p


def _sort_key(row: tuple, places: int):
    key = []
    for v in row:
        if v is None:
            key.append((2, 0.0, ""))
        elif _is_num(v):
            key.append((0, round(float(v), places), ""))
        else:
            key.append((1, 0.0, str(v)))
    return tuple(key)


def _rows_equal(g: list[tuple], p: list[tuple], ordered: bool, places: int) -> bool:
    if len(g) != len(p):
        return False
    if not ordered:
        g = sorted(g, key=lambda r: _sort_key(r, places))
        p = sorted(p, key=lambda r: _sort_key(r, places))
    return all(len(a) == len(b) and all(_same(x, y, places) for x, y in zip(a, b)) for a, b in zip(g, p))


MAX_PREDICTED_COLUMNS = 8


def results_match(gold_rows, pred_rows, ordered: bool = False, places: int = 2) -> bool:
    """True if the predicted result holds the gold result.

    Column names are ignored. The predicted result may have extra columns and may order its columns differently, as long as some
    choice of its columns reproduces the gold rows. Integers must match exactly; other numbers match within half a unit of the
    last compared decimal place. Row order is compared only when `ordered` is true. A date at midnight equals its date.
    """
    g = [tuple(_norm(v) for v in r) for r in gold_rows]
    p = [tuple(_norm(v) for v in r) for r in pred_rows]
    if len(g) != len(p):
        return False
    if not g:
        return True
    ng, npred = len(g[0]), len(p[0])
    if npred < ng or npred > MAX_PREDICTED_COLUMNS:
        return False
    for cols in itertools.permutations(range(npred), ng):
        if _rows_equal(g, [tuple(r[i] for i in cols) for r in p], ordered, places):
            return True
    return False


# ---------------------------------------------------------------- scoring one answer

@dataclass
class Prediction:
    """What an agent produced for one question: a refusal, or the rows its final successful query returned."""
    refused: bool = False
    rows: list[tuple] | None = None


OUTCOMES = ("correct", "correct_refusal", "wrong_answer", "false_refusal", "should_have_refused", "no_result")
GOOD = ("correct", "correct_refusal")


def score(q: Question, gold_rows: list[tuple] | None, pred: Prediction | None) -> str:
    if not q.answerable:
        return "correct_refusal" if pred is not None and pred.refused else "should_have_refused"
    if pred is None or (not pred.refused and pred.rows is None):
        return "no_result"
    if pred.refused:
        return "false_refusal"
    return "correct" if results_match(gold_rows, pred.rows, q.ordered, q.places) else "wrong_answer"


def summarise(questions: list[Question], outcomes: dict[str, str]) -> dict:
    """Accuracy overall and by kind. Every question must have an outcome, so a partial run cannot pass as a full one."""
    missing = [q.id for q in questions if q.id not in outcomes]
    if missing:
        raise ValueError(f"no outcome for questions: {missing}")
    bad = {o for o in outcomes.values() if o not in OUTCOMES}
    if bad:
        raise ValueError(f"unknown outcomes: {sorted(bad)}")

    def rate(qs: list[Question]) -> dict:
        n = len(qs)
        ok = sum(outcomes[q.id] in GOOD for q in qs)
        return {"n": n, "correct": ok, "rate": round(ok / n, 4) if n else None}

    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]
    counts = {o: sum(outcomes[q.id] == o for q in questions) for o in OUTCOMES}
    return {
        "overall": rate(questions),
        "by_kind": {k: rate([q for q in questions if q.kind == k]) for k in KINDS if any(q.kind == k for q in questions)},
        "answerable_accuracy": rate(answerable),
        "refusal_correctness": rate(unanswerable),
        "false_refusal_rate": round(counts["false_refusal"] / len(answerable), 4) if answerable else None,
        "outcome_counts": counts,
    }


# ---------------------------------------------------------------- retrieval scoring

def recall(needs: tuple[str, ...] | list[str], retrieved: list[str]) -> float | None:
    if not needs:
        return None
    return len(set(needs) & set(retrieved)) / len(needs)


def evaluate_retrieval(questions: list[Question], retrieve) -> dict:
    """`retrieve(question_text) -> list of chunk ids`. Only answerable questions that need something are scored.

    mean_recall is the average share of needed chunks retrieved. complete is the share of questions where EVERY needed chunk
    was retrieved, which is what the agent actually depends on. words is the average size of what the agent would be shown.
    """
    texts = {c.id: c.text for c in chunks()}
    rows = []
    for q in questions:
        if not (q.answerable and q.needs):
            continue
        got = retrieve(q.question)
        rows.append({"id": q.id, "needs": list(q.needs), "got": got, "recall": recall(q.needs, got),
                     "missed": sorted(set(q.needs) - set(got)), "words": sum(len(texts[i].split()) for i in got)})
    n = len(rows)

    def part(prefix: str) -> float | None:
        """Mean recall over only the needed chunks of one kind (rules or metrics), for questions that need that kind."""
        vals = []
        for r in rows:
            want = [i for i in r["needs"] if i.startswith(prefix)]
            if want:
                vals.append(len(set(want) & set(r["got"])) / len(want))
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "questions": n,
        "mean_recall": round(sum(r["recall"] for r in rows) / n, 4) if n else None,
        "complete": round(sum(not r["missed"] for r in rows) / n, 4) if n else None,
        "rule_recall": part("rule:"),
        "metric_recall": part("metric:"),
        "avg_words": round(sum(r["words"] for r in rows) / n, 1) if n else None,
        "per_question": rows,
    }
