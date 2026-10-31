"""BM25 retrieval over the data-dictionary chunks. Pure Python, no model and no API.

Embeddings are added only if the retrieval evaluation shows BM25 missing the definitions people need.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .dictionary import Chunk

_STOP = frozenset("a an and are as at be by for from has have how i in is it its of on or per that the their there this to was what when which who with".split())


def tokenize(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):        # splits net_revenue into net, revenue
        if w in _STOP:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class Retriever:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self._docs = [Counter(tokenize(f"{c.title} {c.title} {c.text}")) for c in chunks]   # title counted twice: names matter
        self._len = [sum(d.values()) for d in self._docs]
        self._avg = sum(self._len) / len(self._len)
        df: Counter = Counter()
        for d in self._docs:
            df.update(d.keys())
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 3) -> list[Hit]:
        terms = [t for t in tokenize(query) if t in self._idf]
        scored = []
        for i, doc in enumerate(self._docs):
            s = 0.0
            for t in terms:
                f = doc.get(t, 0)
                if f:
                    s += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self._len[i] / self._avg))
            if s > 0:
                scored.append(Hit(self.chunks[i], s))
        scored.sort(key=lambda h: (-h.score, h.chunk.id))
        return scored[:k]
