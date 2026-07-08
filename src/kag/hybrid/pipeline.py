"""End-to-end HybridRAG pipeline.

:class:`HybridPipeline` wires the four components
(classifier → boundary → retrievers → RRF → evidence) into one
``run`` call. The API surface (``kag.api.hybrid``) just adapts the
HTTP request into :class:`HybridRequest` and forwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from kag.db.arango import ArangoStore
from kag.hybrid.boundary import check as boundary_check
from kag.hybrid.classifier import classify, retriever_weights
from kag.hybrid.evidence import build_response
from kag.hybrid.fusion import fuse
from kag.hybrid.retrievers import GraphRetriever, VectorRetriever
from kag.vector_store.writer import QdrantWriter

log = structlog.get_logger("kag.hybrid.pipeline")


@dataclass
class HybridRequest:
    query: str
    kb_key: str
    kb_ontology_version: int | None = None
    top_k: int = 10
    top_n: int = 5
    include_evidence: bool = True


class HybridPipeline:
    def __init__(
        self,
        *,
        arango: ArangoStore | None = None,
        qdrant: QdrantWriter | None = None,
        vector_retriever: VectorRetriever | None = None,
        graph_retriever: GraphRetriever | None = None,
    ) -> None:
        self._arango = arango or ArangoStore()
        self._qdrant = qdrant or QdrantWriter()
        self._vector = vector_retriever or VectorRetriever()
        self._graph = graph_retriever or GraphRetriever(arango=self._arango)

    async def run(self, req: HybridRequest) -> dict[str, Any]:
        boundary_check(
            arango=self._arango,
            kb_key=req.kb_key,
            kb_ontology_version=req.kb_ontology_version,
        )
        qtype = classify(req.query)
        vw, gw = retriever_weights(qtype)
        log.info(
            "hybrid.classify",
            kb_key=req.kb_key,
            query_type=str(qtype),
            vw=vw,
            gw=gw,
        )

        # Vector path (async — embed call)
        vector_results = await self._vector.retrieve(
            kb_key=req.kb_key,
            writer=self._qdrant,
            query=req.query,
            top_k=req.top_k,
        )

        # Graph path (sync, in ArangoStore)
        graph_result = self._graph.retrieve(
            kb_key=req.kb_key,
            query=req.query,
            top_k=req.top_k,
        )

        items = fuse(
            vector_results=vector_results,
            graph_nodes=graph_result["nodes"],
            vector_weight=vw,
            graph_weight=gw,
            top_n=req.top_n,
        )
        response = build_response(
            arango=self._arango,
            items=items,
            query_type=str(qtype),
            top_n=req.top_n,
        )
        response["edges"] = graph_result["edges"]
        response["matched_names"] = graph_result["matched_names"]
        log.info(
            "hybrid.done",
            kb_key=req.kb_key,
            candidates=len(items),
            evidence=len(response["evidence"]),
            conflicts=len(response["conflicts"]),
        )
        return response
