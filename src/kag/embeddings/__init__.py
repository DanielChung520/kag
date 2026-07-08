"""Embedding service — thin wrapper around the LLM client."""

from __future__ import annotations

from kag.embeddings.service import Embedder, get_embedder

__all__ = ["Embedder", "get_embedder"]
