"""BM25 retrieval (self-contained, no external deps).

Implements the classic Robertson/Walker BM25 scoring used as the lexical leg of
hybrid retrieval. Used as a DB-free fallback and as the BM25 component fused with
vector search via reciprocal rank fusion.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Bm25Index:
    documents: list[dict[str, object]] = field(default_factory=list)
    _doc_tokens: list[Counter] = field(default_factory=list)
    _doc_len: list[int] = field(default_factory=list)
    _doc_freq: Counter = field(default_factory=Counter)
    _avg_doc_len: float = 0.0
    k1: float = 1.5
    b: float = 0.75

    def add_documents(self, docs: Iterable[dict[str, object]]) -> None:
        for doc in docs:
            self.add_document(doc)

    def add_document(self, doc: dict[str, object]) -> None:
        content = str(doc.get("content") or "")
        tokens = self.tokenize(content)
        self._doc_tokens.append(Counter(tokens))
        self._doc_len.append(max(len(tokens), 1))
        for token in set(tokens):
            self._doc_freq[token] += 1
        self.documents.append(doc)
        total = sum(self._doc_len)
        self._avg_doc_len = total / len(self._doc_len)

    def tokenize(self, text: str) -> list[str]:
        toks = []
        current: list[str] = []
        for ch in (text or "").lower():
            if ch.isalnum():
                current.append(ch)
            else:
                if current:
                    toks.append("".join(current))
                    current = []
        if current:
            toks.append("".join(current))
        return toks

    def score(self, query: str, doc_idx: int) -> float:
        q_tokens = self.tokenize(query)
        scores = 0.0
        n = len(self._doc_len)
        doc_counter = self._doc_tokens[doc_idx]
        doc_len = self._doc_len[doc_idx]
        for token in q_tokens:
            f = doc_counter.get(token, 0)
            if f == 0:
                continue
            df = self._doc_freq.get(token, 0)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            tf_norm = (f * (self.k1 + 1)) / (
                f + self.k1 * (1 - self.b + self.b * doc_len / self._avg_doc_len)
            )
            scores += idf * tf_norm
        return scores

    def search(self, query: str, top_k: int = 10) -> list[float]:
        scored = []
        for idx in range(len(self._doc_len)):
            s = self.score(query, idx)
            if s > 0 or self._doc_len[idx] > 0:
                scored.append(s)
        top_n = sorted(range(len(scored)), key=lambda i: scored[i], reverse=True)[:top_k]
        return list(top_n)