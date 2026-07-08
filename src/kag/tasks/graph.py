"""Graph extraction Celery task.

Reads the file's chunks from ArangoDB ``kag_chunks`` and runs the
ontology-aware LLM extractor. The extractor is responsible for
entity / relation dedup (see :mod:`kag.graph.extractor`).
"""

from __future__ import annotations

import contextlib
from typing import Any

import structlog

from kag.config import get_settings
from kag.db.arango import ArangoStore
from kag.graph.extractor import extract_entities_relations
from kag.llm.client import LLMClient
from kag.tasks.celery_app import celery_app

log = structlog.get_logger("kag.tasks.graph")


def _load_chunks(arango: ArangoStore, file_id: str) -> list[dict[str, Any]]:
    rows = arango.query_all(
        "FOR c IN kag_chunks FILTER c.file_id == @fid SORT c.chunk_index RETURN c",
        bind_vars={"fid": file_id},
    )
    return list(rows or [])


@celery_app.task(name="kag.tasks.graph.graph_task", bind=True)  # type: ignore[untyped-decorator]
def graph_task(
    self: Any,
    file_id: str,
    kb_key: str,
    ontology_name: str = "",
) -> dict[str, Any]:
    """Extract entities + relations; deduped against prior extractions."""
    log.info("graph.start", file_id=file_id, kb_key=kb_key, ontology_name=ontology_name)
    settings = get_settings()
    arango = ArangoStore()
    llm = LLMClient()

    chunks = _load_chunks(arango, file_id)
    if not chunks:
        return {"ok": False, "error": "no chunks", "entities": 0, "relations": 0}

    result = extract_entities_relations(
        arango=arango,
        llm=llm,
        model=settings.GRAPH_MODEL,
        temperature=settings.LLM_TEMPERATURE_GRAPH,
        max_tokens=settings.LLM_MAX_TOKENS_GRAPH,
        kb_key=kb_key,
        file_id=file_id,
        chunks=chunks,
        ontology_name=ontology_name,
    )

    with contextlib.suppress(Exception):
        arango.database.collection("kag_files").update(
            {"_key": file_id, "kb_key": kb_key, "status": "graphed"}
        )

    log.info(
        "graph.ok",
        file_id=file_id,
        kb_key=kb_key,
        **result,
    )
    return {"ok": True, "file_id": file_id, **result}
