"""Vector + graph retrievers used by the HybridRAG pipeline.

Both retrievers scope themselves to a single ``kb_key``; the
pipeline enforces that scope again via :func:`kag.hybrid.boundary.check`.

- :class:`VectorRetriever` — embeds the query (or uses a precomputed
  vector) and asks Qdrant for the top-k similar chunks.
- :class:`GraphRetriever` — extracts candidate entity names from the
  query (capitalized tokens, acronyms, multi-char Chinese spans)
  and looks them up in ``kag_graph_nodes``; matching nodes' 1-hop
  neighbourhood in ``kag_graph_edges`` is the result.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from qdrant_client.http.models import ScoredPoint

from kag.db.arango import ArangoStore
from kag.embeddings.service import Embedder

log = structlog.get_logger("kag.hybrid.retrievers")

_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z][a-zA-Z0-9_-]{2,}\b"),
    re.compile(r"\b[A-Z]{2,}\b"),
    re.compile(r"[\u4e00-\u9fff]{2,}[A-Za-z0-9]"),
    re.compile(r"[\u4e00-\u9fff]{4,}"),
)


def _candidate_entity_names(query: str) -> list[str]:
    """Heuristic: pull spans that look like entity names out of the query."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in _ENTITY_PATTERNS:
        for m in pat.finditer(query):
            name = m.group(0).strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append(name)
    return out


class VectorRetriever:
    """Embed the query and search the per-KB Qdrant collection."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder or Embedder()

    async def retrieve(
        self,
        *,
        kb_key: str,
        writer: Any,
        query: str,
        top_k: int = 10,
    ) -> list[ScoredPoint]:
        vectors = await self._embedder.embed([query])
        return list(
            writer.search(
                kb_key=kb_key,
                query_vector=vectors[0],
                top_k=top_k,
            )
        )


class GraphRetriever:
    """Find entity names in the query → walk 1-hop in the graph."""

    def __init__(self, arango: ArangoStore | None = None) -> None:
        self._arango = arango or ArangoStore()

    def retrieve(
        self,
        *,
        kb_key: str,
        query: str,
        top_k: int = 10,
    ) -> dict[str, Any]:
        names = _candidate_entity_names(query)
        if not names:
            return {"nodes": [], "edges": [], "matched_names": []}
        # Look up nodes by name (case-insensitive on the persisted name).
        node_rows = (
            self._arango.query_all(
                """
            FOR n IN kag_graph_nodes
              FILTER n.kb_key == @kb
                AND LOWER(n.name) IN @names_lc
              RETURN n
            """,
                bind_vars={"kb": kb_key, "names_lc": [n.lower() for n in names]},
            )
            or []
        )
        if not node_rows:
            return {"nodes": [], "edges": [], "matched_names": []}
        edge_rows = (
            self._arango.query_all(
                """
            FOR e IN kag_graph_edges
              FILTER e.kb_key == @kb
                AND (e.from_node IN @nodes OR e.to_node IN @nodes)
              LIMIT @top_k
              RETURN e
            """,
                bind_vars={
                    "kb": kb_key,
                    "nodes": [n["name"] for n in node_rows],
                    "top_k": top_k,
                },
            )
            or []
        )
        return {
            "nodes": node_rows[:top_k],
            "edges": edge_rows,
            "matched_names": [n["name"] for n in node_rows],
        }


def _norm(name: str) -> str:
    return re.sub(r"\s+", "_", name.strip()).lower()
