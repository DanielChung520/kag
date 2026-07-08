"""Query classifier: route a query to the right retriever blend.

The HybridRAG pipeline (Wave 8) supports three query types:

- **structure** — asks about relationships, hierarchies, "X 跟 Y 的
  關係" / "depends on" / "connected to"
- **entity** — names a specific thing, expects a direct lookup
- **semantic** — open-ended, similarity search is the right tool

The classifier is a small heuristic over the query text — no LLM
call (that would dominate latency for a "fast" hybrid path). The
weight vector the pipeline uses to fuse the two retrievers depends
on the classified type; "structure" pulls more weight toward
graph, "entity" toward direct lookup, "semantic" toward vector.

Unknown / fuzzy queries default to ``semantic``.
"""

from __future__ import annotations

import re
from enum import StrEnum


class QueryType(StrEnum):
    STRUCTURE = "structure"
    ENTITY = "entity"
    SEMANTIC = "semantic"


_STRUCTURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"關係|relationship|連|connect|related|depend|屬於|屬|歸|影響|impact", re.I),
    re.compile(r"\b(depend|relate|connect|impact|influence|hierarchy|tree|graph)\b", re.I),
    re.compile(r"how\s+does\s+.+\s+(relate|connect|depend|impact)", re.I),
    re.compile(r"between\s+\w+\s+and\s+\w+", re.I),
)

_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z][a-zA-Z0-9_-]{2,}\b"),  # CamelCase / multi-cap tokens
    re.compile(r"\b[A-Z]{2,}\b"),  # IBM-style acronyms
    re.compile(r"[\u4e00-\u9fff]{2,}[A-Za-z0-9]"),  # Chinese + Latin alnum (e.g. 台北101)
    re.compile(r"[\u4e00-\u9fff]{4,}"),  # 4+ Chinese chars in a row
)


def classify(query: str) -> QueryType:
    """Return the classified query type for a raw query string."""
    text = query.strip()
    if not text:
        return QueryType.SEMANTIC

    structure_hits = sum(1 for p in _STRUCTURE_PATTERNS if p.search(text))
    entity_hits = sum(1 for p in _ENTITY_PATTERNS if p.search(text))

    if structure_hits >= 1 and structure_hits >= entity_hits:
        return QueryType.STRUCTURE
    if entity_hits >= 2:
        return QueryType.ENTITY
    return QueryType.SEMANTIC


def retriever_weights(query_type: QueryType) -> tuple[float, float]:
    """Return ``(vector_weight, graph_weight)`` for a given query type.

    Sums to 1.0. Heuristic; tune in production from real query logs.
    """
    if query_type == QueryType.STRUCTURE:
        return 0.4, 0.6
    if query_type == QueryType.ENTITY:
        return 0.6, 0.4
    return 0.7, 0.3
