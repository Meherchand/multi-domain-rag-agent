"""Offline, deterministic embeddings for demo mode and tests.

This is a hashed bag-of-words projection, not a learned model. It has no
semantic understanding: two paraphrases that share no vocabulary will not be
close. What it *does* give you is a stable, dependency-free vector space, which
is enough to exercise indexing, similarity search, ranking and the whole
request path without calling an embedding provider.

Term overlap still retrieves sensibly, so the demo corpus returns plausible
passages for keyword-ish questions. Use a real provider for anything that
depends on semantic similarity.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from langchain_core.embeddings import Embeddings

_TOKEN = re.compile(r"[a-z0-9]+")

# Crude suffix stripping, applied longest-first. It is not a real stemmer, but
# it collapses the inflections that otherwise sink demo retrieval — a question
# asking how long something is "reserved" should match a passage about
# "reservations".
_SUFFIXES = (
    "ization",
    "isation",
    "ations",
    "ements",
    "ingly",
    "ation",
    "ement",
    "ions",
    "ing",
    "ers",
    "ies",
    "ed",
    "es",
    "er",
    "s",
)


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


class HashingEmbeddings(Embeddings):
    def __init__(self, dimensions: int = 384, max_chars: int = 6000):
        if dimensions < 8:
            raise ValueError("dimensions must be >= 8")
        self.dimensions = dimensions
        self.max_chars = max_chars

    def _vector(self, text: str) -> list[float]:
        tokens = [_stem(t) for t in _TOKEN.findall(text[: self.max_chars].lower())]
        vec = [0.0] * self.dimensions
        if not tokens:
            return vec

        counts = Counter(tokens)
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            # Sublinear term weighting, as in classic tf-idf, so that a term
            # repeated many times does not dominate the vector.
            vec[bucket] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)
