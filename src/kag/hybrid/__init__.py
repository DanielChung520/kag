"""HybridRAG subsystem: classifier, retrievers, RRF, boundary, evidence.

Top-level orchestrator: :func:`kag.hybrid.pipeline.run`.
"""

from __future__ import annotations

from kag.hybrid import boundary, classifier, evidence, fusion, pipeline, retrievers
from kag.hybrid.boundary import BoundaryViolationError
from kag.hybrid.classifier import QueryType, classify, retriever_weights
from kag.hybrid.evidence import build_response
from kag.hybrid.fusion import FusedItem, fuse
from kag.hybrid.pipeline import HybridPipeline, HybridRequest
from kag.hybrid.retrievers import GraphRetriever, VectorRetriever

__all__ = [
    "BoundaryViolationError",
    "FusedItem",
    "GraphRetriever",
    "HybridPipeline",
    "HybridRequest",
    "QueryType",
    "VectorRetriever",
    "boundary",
    "build_response",
    "classifier",
    "classify",
    "evidence",
    "fuse",
    "fusion",
    "pipeline",
    "retriever_weights",
    "retrievers",
]
