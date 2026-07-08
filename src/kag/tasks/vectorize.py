"""Vectorize task: SeaweedFS file → parse → chunk → embed → Qdrant + ArangoDB.

Wave 5 ships a text-only path (``.txt`` / ``.md`` / ``text/*`` MIME).
PDF / DOCX / image extraction land in Wave 6 — when an
unsupported file type is encountered we mark the file
``status=failed`` with a clear ``error_msg`` rather than silently
skipping.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from qdrant_client.http import models as qmodels

from kag.config import get_settings
from kag.db.arango import ArangoStore
from kag.db.qdrant import QdrantStore
from kag.db.seaweedfs import SeaweedStore
from kag.llm.client import LLMClient
from kag.tasks.celery_app import celery_app

log = structlog.get_logger("kag.tasks.vectorize")

# Defaults match Settings.KAG_VECTOR_CHUNK_SIZE/OVERLAP when present.
_settings = get_settings()
CHUNK_SIZE = _settings.KAG_VECTOR_CHUNK_SIZE
CHUNK_OVERLAP = _settings.KAG_VECTOR_CHUNK_OVERLAP

_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".log", ".csv")
_UNSUPPORTED_SUFFIXES = (".pdf", ".docx", ".doc", ".pptx", ".xlsx")


def _looks_like_text(mime: str, filename: str) -> bool:
    return (
        mime.startswith(_TEXT_MIME_PREFIXES) or filename.lower().endswith(_TEXT_EXTENSIONS)
    ) and not filename.lower().endswith(_UNSUPPORTED_SUFFIXES)


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Greedy character-window chunker; simple, deterministic, good enough
    for Wave 5. Wave 7 will swap in a structure-aware chunker."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    step = max(1, size - overlap)
    for start in range(0, len(text), step):
        piece = text[start : start + size]
        if piece.strip():
            chunks.append(piece)
        if start + size >= len(text):
            break
    return chunks


@celery_app.task(name="kag.tasks.vectorize.vectorize_task", bind=True)  # type: ignore[untyped-decorator]
def vectorize_task(self: Any, file_id: str, kb_key: str) -> dict[str, Any]:
    """Parse + chunk + embed + upsert for one uploaded file.

    Returns a small status dict for the job record. All errors are
    caught at the top level and reflected in the file's
    ``error_msg`` so the UI can show them.
    """
    log.info("vectorize.start", file_id=file_id, kb_key=kb_key)
    settings = get_settings()
    arango = ArangoStore()
    qdrant = QdrantStore(settings=settings)
    seaweed = SeaweedStore()
    llm = LLMClient()
    qdrant_collection = QdrantStore.collection_name(kb_key)

    # 1. Load file metadata
    file_row = arango.query_one(
        "FOR f IN kag_files FILTER f._key == @fid RETURN f",
        bind_vars={"fid": file_id},
    )
    if file_row is None:
        msg = f"file {file_id!r} not found"
        log.error("vectorize.no_file", file_id=file_id)
        return {"ok": False, "error": msg, "chunks": 0}
    filename = file_row.get("filename", "")
    mime = file_row.get("mime", "application/octet-stream")
    seaweed_key = file_row.get("seaweed_key", "")

    # 2. Mark as processing
    arango.database.collection("kag_files").update(
        {"_key": file_id, "status": "processing", "kb_key": kb_key}
    )

    try:
        # 3. Download
        if not seaweed_key:
            raise ValueError("file has no seaweed_key")
        data = seaweed.download_file(seaweed_key)

        # 4. Parse (text-only for now)
        if not _looks_like_text(mime, filename):
            raise NotImplementedError(
                f"file type {mime!r} / {filename!r} not supported in Wave 5; "
                "PDF/DOCX/image extractors land in Wave 6"
            )
        text = data.decode("utf-8", errors="replace")

        # 5. Chunk
        chunks = _chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
        if not chunks:
            raise ValueError("file contains no extractable text")

        # 6. Embed (async — run synchronously here via asyncio.run)
        embeddings: list[list[float]] = asyncio.run(llm.embed(settings.EMBEDDING_MODEL, chunks))
        if len(embeddings) != len(chunks):
            raise RuntimeError(
                f"embedding count mismatch: {len(embeddings)} vs {len(chunks)} chunks"
            )

        # 7. Ensure Qdrant collection + upsert
        qdrant.ensure_collection(qdrant_collection, dim=settings.QDRANT_VECTOR_DIM)
        points = [
            qmodels.PointStruct(
                id=f"{file_id}_{i}",
                vector=vec,
                payload={
                    "kb_key": kb_key,
                    "file_id": file_id,
                    "doc_id": file_id,
                    "chunk_index": i,
                    "text": chunk,
                },
            )
            for i, (vec, chunk) in enumerate(zip(embeddings, chunks, strict=True))
        ]
        qdrant.upsert_chunks(qdrant_collection, points)

        # 8. Persist chunk metadata to kag_chunks
        coll = arango.database.collection("kag_chunks")
        for i, chunk in enumerate(chunks):
            coll.insert(
                {
                    "_key": f"{file_id}_{i}",
                    "kb_key": kb_key,
                    "file_id": file_id,
                    "chunk_index": i,
                    "text": chunk,
                }
            )

        # 9. Mark file as vectorized
        arango.database.collection("kag_files").update(
            {
                "_key": file_id,
                "status": "vectorized",
                "kb_key": kb_key,
                "processed_at": "NOW()",
                "error_msg": None,
            }
        )
        # NOTE: arango's `update` does not run server-side functions; the
        # `processed_at` field will land as the literal string "NOW()".
        # We follow up with a second update to set the real ISO timestamp
        # from Python.
        from datetime import UTC, datetime

        arango.database.collection("kag_files").update(
            {
                "_key": file_id,
                "kb_key": kb_key,
                "status": "vectorized",
                "processed_at": datetime.now(UTC).isoformat(),
                "error_msg": None,
            }
        )

        log.info(
            "vectorize.ok",
            file_id=file_id,
            kb_key=kb_key,
            chunks=len(chunks),
        )
        return {"ok": True, "chunks": len(chunks), "file_id": file_id}

    except Exception as exc:
        log.exception("vectorize.fail", file_id=file_id)
        try:
            arango.database.collection("kag_files").update(
                {
                    "_key": file_id,
                    "kb_key": kb_key,
                    "status": "failed",
                    "error_msg": str(exc)[:1000],
                }
            )
        except Exception:
            log.exception("vectorize.fail_status_update", file_id=file_id)
        return {"ok": False, "error": str(exc), "chunks": 0, "file_id": file_id}
