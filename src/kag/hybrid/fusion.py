"""Reciprocal Rank Fusion (RRF) for hybrid retrieval.

Classic RRF (Cormack et al. 2009): for each candidate that appears
in one or more ranked lists, the fused score is

    fused_score(d) = Σ_i  1 / (k + rank_i(d))

where ``rank_i(d)`` is the 1-based position of ``d`` in the i-th
list and ``k`` is a smoothing constant (default 60, the value used
in the original paper).

We use the chunk's *id* (``f"{file_id}_{chunk_index}"``) for vector
results and the node's *name* for graph results as the join key
across the two lists. The fusion returns a list of
:class:`FusedItem` sorted by descending fused score, each carrying
both per-list ranks and the source payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qdrant_client.http.models import ScoredPoint

DEFAULT_K = 60


@dataclass
class FusedItem:
    """One row of the fused ranking.

    `key` is the join identifier — chunk id for vector results,
    entity name for graph results.
    """

    key: str
    score: float
    vector_rank: int | None = None
    graph_rank: int | None = None
    source: str = ""  # "vector" | "graph" | "both"
    payload: dict[str, Any] = field(default_factory=dict)


def _vector_key(point: ScoredPoint) -> str:
    """Chunk id derived from the Qdrant payload, falling back to point.id."""
    payload = point.payload or {}
    fid = str(payload.get("file_id") or "")
    idx = str(payload.get("chunk_index") or "")
    if fid and idx:
        return f"{fid}_{idx}"
    pid = point.id if isinstance(point.id, str) else str(point.id)
    return pid


def _graph_node_key(node: dict[str, Any]) -> str:
    return str(node.get("name") or "")


def fuse(
    *,
    vector_results: list[ScoredPoint],
    graph_nodes: list[dict[str, Any]],
    vector_weight: float = 0.7,
    graph_weight: float = 0.3,
    k: int = DEFAULT_K,
    top_n: int = 20,
) -> list[FusedItem]:
    """Combine vector + graph results with weighted RRF.

    Each list is weighted by ``vector_weight`` / ``graph_weight``
    *before* the 1/(k+rank) term, so the weights are directly
    comparable. Items in only one list still get that list's
    contribution; items in both get both.
    """
    items: dict[str, FusedItem] = {}

    for rank, point in enumerate(vector_results, start=1):
        key = _vector_key(point)
        contribution = vector_weight * (1.0 / (k + rank))
        existing = items.get(key)
        if existing is None:
            items[key] = FusedItem(
                key=key,
                score=contribution,
                vector_rank=rank,
                source="vector",
                payload=dict(point.payload or {}),
            )
        else:
            existing.vector_rank = rank
            existing.score += contribution
            existing.source = "both"

    for rank, node in enumerate(graph_nodes, start=1):
        key = _graph_node_key(node)
        if not key:
            continue
        contribution = graph_weight * (1.0 / (k + rank))
        existing = items.get(key)
        if existing is None:
            items[key] = FusedItem(
                key=key,
                score=contribution,
                graph_rank=rank,
                source="graph",
                payload=dict(node),
            )
        else:
            existing.graph_rank = rank
            existing.score += contribution
            existing.source = "both"

    ranked = sorted(items.values(), key=lambda it: it.score, reverse=True)
    return ranked[:top_n]
