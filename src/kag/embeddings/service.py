"""Embedding service.

Thin async wrapper around :class:`kag.llm.client.LLMClient.embed`
that adds:

- explicit batch-size chunking (the OpenAI endpoint accepts a batch,
  but very large lists are still better split to keep individual
  request latency bounded)
- a small retry loop for transient httpx errors
- a :attr:`dimension` lookup so callers don't have to know the
  embedding model's output size ahead of time
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import structlog

from kag.config import get_settings
from kag.llm.client import LLMClient

log = structlog.get_logger("kag.embeddings.service")

DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_RETRIES = 2


class Embedder:
    """High-level embedding client.

    Construction reads the embedding model from settings; the
    dimension is queried once on first use (and cached).
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.EMBEDDING_MODEL
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._client = LLMClient()
        self._dimension: int | None = settings.QDRANT_VECTOR_DIM

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = get_settings().QDRANT_VECTOR_DIM
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts``; returns vectors in the same order."""
        if not texts:
            return []
        out: list[list[float]] = []
        for chunk in self._chunks(texts):
            out.extend(await self._embed_with_retry(chunk))
        if len(out) != len(texts):
            raise RuntimeError(f"embed: expected {len(texts)} vectors, got {len(out)}")
        return out

    def _chunks(self, texts: list[str]) -> Iterable[list[str]]:
        for i in range(0, len(texts), self._batch_size):
            yield texts[i : i + self._batch_size]

    async def _embed_with_retry(self, batch: list[str]) -> list[list[float]]:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._client.embed(self._model, batch)
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "embed.retry",
                    attempt=attempt,
                    error=str(exc)[:200],
                )
                await asyncio.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"embed failed after {self._max_retries + 1} attempts: {last_exc}")


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Process-wide :class:`Embedder` singleton."""
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
