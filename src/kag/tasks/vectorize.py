"""Vectorize task: SeaweedFS file → parse → chunk → embed → Qdrant + ArangoDB.

Wave 6 wires this through the full ingestion pipeline (PDF / DOCX /
Markdown / text / image-with-VLM-captioning). For files of an
unsupported type the pipeline raises
:class:`kag.ingestion.extractors.UnsupportedFileTypeError`; we
catch it and mark the file ``status=failed`` with a clear
``error_msg``.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import structlog
from qdrant_client.http import models as qmodels

from kag.config import get_settings
from kag.db.arango import ArangoStore
from kag.db.qdrant import QdrantStore
from kag.db.seaweedfs import SeaweedStore
from kag.ingestion.extractors import UnsupportedFileTypeError
from kag.ingestion.pipeline import process_file
from kag.llm.client import LLMClient
from kag.tasks.celery_app import celery_app

log = structlog.get_logger("kag.tasks.vectorize")


@celery_app.task(name="kag.tasks.vectorize.vectorize_task", bind=True)  # type: ignore[untyped-decorator]
def vectorize_task(self: Any, file_id: str, kb_key: str) -> dict[str, Any]:
    """Parse + chunk + embed + upsert for one uploaded file."""
    log.info("vectorize.start", file_id=file_id, kb_key=kb_key)
    settings = get_settings()
    arango = ArangoStore()
    qdrant = QdrantStore(settings=settings)
    seaweed = SeaweedStore()
    llm = LLMClient()
    qdrant_collection = QdrantStore.collection_name(kb_key)

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

    arango.database.collection("kag_files").update(
        {"_key": file_id, "kb_key": kb_key, "status": "processing", "error_msg": None}
    )

    try:
        if not seaweed_key:
            raise ValueError("file has no seaweed_key")
        data = seaweed.download_file(seaweed_key)

        # 1. Parse + caption + chunk via the Wave 6 pipeline
        chunks = process_file(
            data,
            filename=filename,
            mime=mime,
        )
        if not chunks:
            raise ValueError("file produced no chunks (empty or unsupported)")

        # 2. Embed
        texts = [c["text"] for c in chunks]
        embeddings: list[list[float]] = asyncio.run(llm.embed(settings.EMBEDDING_MODEL, texts))
        if len(embeddings) != len(chunks):
            raise RuntimeError(f"embedding count mismatch: {len(embeddings)} vs {len(chunks)}")

        # 3. Qdrant upsert
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
                    "text": chunk["text"],
                    "page_no": chunk.get("page_no"),
                    "section": chunk.get("section"),
                },
            )
            for i, (vec, chunk) in enumerate(zip(embeddings, chunks, strict=True))
        ]
        qdrant.upsert_chunks(qdrant_collection, points)

        # 4. Persist chunk metadata
        chunks_coll = arango.database.collection("kag_chunks")
        for i, chunk in enumerate(chunks):
            chunks_coll.insert(
                {
                    "_key": f"{file_id}_{i}",
                    "kb_key": kb_key,
                    "file_id": file_id,
                    "chunk_index": i,
                    "text": chunk["text"],
                    "page_no": chunk.get("page_no"),
                    "section": chunk.get("section"),
                }
            )

        # 5. Mark vectorized
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

    except UnsupportedFileTypeError as exc:
        msg = f"unsupported file type: {exc}"
        log.warning("vectorize.unsupported", file_id=file_id, error=msg)
        _mark_failed(arango, file_id, kb_key, msg)
        return {"ok": False, "error": msg, "chunks": 0, "file_id": file_id}
    except Exception as exc:
        log.exception("vectorize.fail", file_id=file_id)
        _mark_failed(arango, file_id, kb_key, str(exc)[:1000])
        return {"ok": False, "error": str(exc), "chunks": 0, "file_id": file_id}


def _mark_failed(arango: ArangoStore, file_id: str, kb_key: str, msg: str) -> None:
    with contextlib.suppress(Exception):
        arango.database.collection("kag_files").update(
            {
                "_key": file_id,
                "kb_key": kb_key,
                "status": "failed",
                "error_msg": msg[:1000],
            }
        )
