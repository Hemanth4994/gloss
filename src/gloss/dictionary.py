"""Load the data dictionary and turn it into retrievable chunks (one per rule, table and metric)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

_PATH = os.path.join(os.path.dirname(__file__), "dictionary.yaml")


@dataclass(frozen=True)
class Chunk:
    id: str          # for example "metric:net_revenue"
    kind: str        # rule | table | metric
    title: str
    text: str        # what the agent may read (never the reference SQL)


def _one_line(s: str) -> str:
    return " ".join(s.split())


@lru_cache(maxsize=1)
def load_dictionary() -> dict:
    with open(_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def chunks() -> list[Chunk]:
    d = load_dictionary()
    out = [Chunk(f"rule:{r['name']}", "rule", r["name"].replace("_", " "), _one_line(r["definition"])) for r in d["rules"]]
    for name, t in d["tables"].items():
        cols = "; ".join(f"{c}: {_one_line(desc)}" for c, desc in t["columns"].items())
        out.append(Chunk(f"table:{name}", "table", f"table {name}", f"{_one_line(t['description'])} Columns. {cols}"))
    for m in d["metrics"]:
        out.append(Chunk(f"metric:{m['name']}", "metric", m["title"], f"{m['title']}. {_one_line(m['definition'])}"))
    return out


def reference_sql(metric: str) -> str:
    for m in load_dictionary()["metrics"]:
        if m["name"] == metric:
            return _one_line(m["sql"])
    raise KeyError(metric)
