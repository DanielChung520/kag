"""Higher-level Qdrant writer for the write path.

`QdrantStore` (`kag.db.qdrant`) is the thin adapter; this module
adds the conventions the write path needs:

- chunk IDs derived from ``(kb_key, file_id, chunk_index)`` so
  re-vectorizing a file is idempotent (upsert, not duplicate)
- search/delete scoped to a KB by default
- delete-by-file / delete-by-kb helpers for KB cleanup

The class is intentionally stateless beyond the underlying
``QdrantClient``; the per-KB collection name is derived from the
key, so two KBs never share a collection.
"""

from __future__ import annotations

from typing import Any

import structlog
from qdrant_client.http import models as qmodels

from kag.config import get_settings
from kag.db.qdrant import QdrantStore

log = structlog.get_logger("kag.vector_store.writer")


class QdrantWriter:
    """Per-KB-scoped Qdrant write/read helpers."""

    def __init__(self) -> None:
        self._store = QdrantStore(settings=get_settings())

    @staticmethod
    def _point_id(kb_key: str, file_id: str, chunk_index: int) -> str:
        return f"{kb_key}__{file_id}__{chunk_index}"

    def ensure_collection_for_kb(self, kb_key: str) -> str:
        """Create (idempotently) the per-KB collection, return its name."""
        name = QdrantStore.collection_name(kb_key)
        dim = get_settings().QDRANT_VECTOR_DIM
        self._store.ensure_collection(name, dim=dim)
        return name

    def upsert_chunks(
        self,
        kb_key: str,
        file_id: str,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> None:
        """Upsert ``chunks`` for a single file.

        ``chunks`` is a list of dicts each with at least ``text``;
        the ``page_no`` and ``section`` fields are forwarded as
        payload when present.
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"upsert_chunks: {len(chunks)} chunks vs {len(vectors)} vectors")
        if not chunks:
            return
        collection = self.ensure_collection_for_kb(kb_key)
        points = [
            qmodels.PointStruct(
                id=self._point_id(kb_key, file_id, i),
                vector=vec,
                payload={
                    "kb_key": kb_key,
                    "file_id": file_id,
                    "doc_id": file_id,
                    "chunk_index": i,
                    "text": chunk["text"],
                    "page_no": chunk.get("page_no"),
                    "section": chunk.get("section"),
                },
            )
            for i, (vec, chunk) in enumerate(zip(vectors, chunks, strict=True))
        ]
        self._store.upsert_chunks(collection, points)
        log.info(
            "writer.upserted",
            kb_key=kb_key,
            file_id=file_id,
            chunks=len(points),
        )

    def search(
        self,
        kb_key: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        file_id: str | None = None,
    ) -> list[Any]:
        collection = QdrantStore.collection_name(kb_key)
        return self._store.search(
            collection,
            query_vector,
            top_k=top_k,
            kb_key=kb_key if file_id is None else None,
            file_id=file_id,
        )

    def delete_file(self, kb_key: str, file_id: str) -> None:
        collection = QdrantStore.collection_name(kb_key)
        self._store.delete_by_filter(collection, file_id=file_id)

    def delete_kb(self, kb_key: str) -> None:
        collection = QdrantStore.collection_name(kb_key)
        self._store.delete_by_filter(collection, kb_key=kb_key)
