"""HybridRAG endpoints (per-KB API key auth).

| Method | Path | Auth |
|---|---|---|
| POST | /api/v1/hybrid/search | KB Key |
| POST | /api/v1/hybrid/evidence | KB Key |

Both endpoints share the :class:`kag.hybrid.pipeline.HybridPipeline`;
``/evidence`` is a strict superset that *always* includes the
``evidence`` list (the search endpoint may skip it when the
caller asks for "fast" semantic-only results).
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from kag.auth.dependencies import current_kb
from kag.hybrid.boundary import BoundaryViolationError
from kag.hybrid.pipeline import HybridPipeline, HybridRequest
from kag.models import KnowledgeBase

log = structlog.get_logger("kag.api.hybrid")
router = APIRouter(prefix="/api/v1/hybrid", tags=["hybrid"])


class HybridSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    top_n: int = Field(default=5, ge=1, le=20)
    kb_ontology_version: int | None = None
    include_evidence: bool = True


class EvidenceItem(BaseModel):
    key: str
    kind: str  # "chunk" | "entity"
    score: float
    text: str
    source: dict[str, Any]
    ranks: dict[str, int | None]


class HybridSearchResponse(BaseModel):
    query_type: str
    evidence: list[EvidenceItem] = []
    conflicts: list[dict[str, Any]] = []
    matched_names: list[str] = []
    edges: list[dict[str, Any]] = []
    total_candidates: int = 0


def _to_response(result: dict[str, Any]) -> HybridSearchResponse:
    """Coerce the pipeline's loose ``dict`` output into the typed response.

    The pipeline returns ``dict[str, Any]`` for flexibility; the
    API surface needs strict typed fields. Each list is validated
    against its item model and silently dropped if it doesn't fit.
    """
    evidence: list[EvidenceItem] = []
    for item in result.get("evidence", []) or []:
        if isinstance(item, dict):
            try:
                evidence.append(EvidenceItem(**item))
            except Exception:
                continue
    return HybridSearchResponse(
        query_type=str(result.get("query_type", "semantic")),
        evidence=evidence,
        conflicts=list(result.get("conflicts", []) or []),
        matched_names=list(result.get("matched_names", []) or []),
        edges=list(result.get("edges", []) or []),
        total_candidates=int(result.get("total_candidates", 0) or 0),
    )


def _require_kb_match(kb_key: str, caller: KnowledgeBase) -> None:
    if caller.kb_key != kb_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


@router.post("/search", response_model=HybridSearchResponse)
async def hybrid_search(
    body: HybridSearchRequest,
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> HybridSearchResponse:
    """Run the full HybridRAG pipeline for one query."""
    _require_kb_match(caller.kb_key, caller)
    pipeline = HybridPipeline()
    req = HybridRequest(
        query=body.query,
        kb_key=caller.kb_key,
        kb_ontology_version=body.kb_ontology_version,
        top_k=body.top_k,
        top_n=body.top_n,
        include_evidence=body.include_evidence,
    )
    try:
        result = await pipeline.run(req)
    except BoundaryViolationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    log.info(
        "hybrid.search",
        kb_key=caller.kb_key,
        query_type=result.get("query_type"),
        evidence=len(result.get("evidence") or []),
    )
    return _to_response(result)


@router.post("/evidence", response_model=HybridSearchResponse)
async def hybrid_evidence(
    body: HybridSearchRequest,
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> HybridSearchResponse:
    """Same as ``/search`` but always populates ``evidence``."""
    _require_kb_match(caller.kb_key, caller)
    body.include_evidence = True
    pipeline = HybridPipeline()
    req = HybridRequest(
        query=body.query,
        kb_key=caller.kb_key,
        kb_ontology_version=body.kb_ontology_version,
        top_k=body.top_k,
        top_n=body.top_n,
        include_evidence=True,
    )
    try:
        result = await pipeline.run(req)
    except BoundaryViolationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    log.info(
        "hybrid.evidence",
        kb_key=caller.kb_key,
        query_type=result.get("query_type"),
        evidence=len(result.get("evidence") or []),
    )
    return _to_response(result)
