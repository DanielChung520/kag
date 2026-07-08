"""Evidence analyzer: assemble the final response from fused items.

Responsibilities:

- Take the top-N :class:`kag.hybrid.fusion.FusedItem` and pull the
  underlying chunk text (vector path) or entity record (graph
  path) for each.
- Optionally enrich with conflict flags when two vector chunks
  mention the same entity with conflicting types or descriptions
  (simple in-memory detection — a real implementation will use
  a dedicated ``kag_evidence_conflict`` collection).
- Cap evidence at ``max_items`` and return the structured
  response the API surface serializes.
"""

from __future__ import annotations

from typing import Any

import structlog

from kag.db.arango import ArangoStore
from kag.hybrid.fusion import FusedItem

log = structlog.get_logger("kag.hybrid.evidence")


def _chunks_by_key(arango: ArangoStore, keys: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-fetch the chunks referenced by vector results."""
    if not keys:
        return {}
    rows = arango.query_all(
        """
        FOR c IN kag_chunks
          FILTER c._key IN @keys
          RETURN c
        """,
        bind_vars={"keys": keys},
    )
    return {r["_key"]: dict(r) for r in rows or []}


def _detect_conflicts(
    items: list[FusedItem],
) -> list[dict[str, Any]]:
    """Flag vector items that disagree on the same entity's type.

    Heuristic: for items sourced from the graph, look at the
    ``type`` field on the payload. Two items for the same entity
    name with different non-empty types → conflict.
    """
    by_name: dict[str, set[str]] = {}
    for it in items:
        payload_type = (it.payload.get("type") or "").strip()
        if not payload_type or payload_type == "unknown":
            continue
        name = (it.payload.get("name") or "").strip()
        if not name:
            continue
        by_name.setdefault(name, set()).add(payload_type)
    conflicts: list[dict[str, Any]] = []
    for name, types in by_name.items():
        if len(types) > 1:
            conflicts.append({"entity": name, "types": sorted(types)})
    return conflicts


def build_response(
    *,
    arango: ArangoStore,
    items: list[FusedItem],
    query_type: str,
    top_n: int = 5,
) -> dict[str, Any]:
    """Assemble the final hybrid response payload.

    Returns a dict shaped to match the API's Pydantic response
    model. The caller (``kag.api.hybrid``) is responsible for
    wrapping it in the response model.
    """
    evidence: list[dict[str, Any]] = []
    chunk_keys = [it.key for it in items if it.source == "vector"]
    chunks = _chunks_by_key(arango, chunk_keys)
    for it in items[:top_n]:
        if it.source in {"vector", "both"}:
            chunk = chunks.get(it.key, {})
            evidence.append(
                {
                    "key": it.key,
                    "kind": "chunk",
                    "score": it.score,
                    "text": chunk.get("text", ""),
                    "source": {
                        "file_id": chunk.get("file_id"),
                        "chunk_index": chunk.get("chunk_index"),
                        "page_no": chunk.get("page_no"),
                        "section": chunk.get("section"),
                    },
                    "ranks": {
                        "vector": it.vector_rank,
                        "graph": it.graph_rank,
                    },
                }
            )
        else:
            node = it.payload
            evidence.append(
                {
                    "key": it.key,
                    "kind": "entity",
                    "score": it.score,
                    "text": (
                        f"{node.get('name', '')}"
                        + (f" ({node.get('type')})" if node.get("type") else "")
                    ).strip(),
                    "source": {
                        "file_id": node.get("file_id"),
                        "type": node.get("type"),
                        "description": node.get("description"),
                    },
                    "ranks": {
                        "vector": it.vector_rank,
                        "graph": it.graph_rank,
                    },
                }
            )

    return {
        "query_type": query_type,
        "evidence": evidence,
        "conflicts": _detect_conflicts(items),
        "total_candidates": len(items),
    }
