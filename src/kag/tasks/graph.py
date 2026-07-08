"""Graph extraction task: chunks + ontology → LLM extract → kag_graph_*.

Wave 5 ships a minimal version that:

1. Loads chunks for the given ``file_id`` from ``kag_chunks``.
2. Calls the LLM (``GRAPH_MODEL``) once per chunk with a
   ``json_mode=True`` prompt asking for entities + relations.
3. Upserts the extracted entities to ``kag_graph_nodes`` and
   relations to ``kag_graph_edges`` (deduplicated by ``(name)`` and
   ``(from_node, to_node, label)`` respectively).

The prompt format and parsing are intentionally simple — a
production implementation will replace the prompt template with
an ontology-aware one (Wave 7, task 38) and add proper JSON
validation + retry handling.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from celery import (
    shared_task,  # type: ignore[import-untyped]  # celery lacks PEP 561 stubs  # noqa: F401
)

from kag.config import get_settings
from kag.db.arango import ArangoStore
from kag.llm.client import LLMClient
from kag.tasks.celery_app import celery_app

log = structlog.get_logger("kag.tasks.graph")

_EXTRACTION_PROMPT = """\
You are an information-extraction assistant. Given a text chunk and
a list of entity types, extract every distinct entity and every
relation between them.

Entity types: {entity_types}

Output a JSON object with this exact shape:
{{
  "entities": [
    {{"name": "...", "type": "...", "description": "..."}}
  ],
  "relations": [
    {{"from": "<entity name>", "to": "<entity name>", "label": "<verb>"}}
  ]
}}

TEXT:
{text}
"""


def _load_chunks(arango: ArangoStore, file_id: str) -> list[dict[str, Any]]:
    """Return chunks for ``file_id`` ordered by chunk_index."""
    rows = arango.query_all(
        "FOR c IN kag_chunks FILTER c.file_id == @fid SORT c.chunk_index RETURN c",
        bind_vars={"fid": file_id},
    )
    return list(rows or [])


def _upsert_nodes(
    arango: ArangoStore,
    kb_key: str,
    file_id: str,
    entities: list[dict[str, Any]],
) -> int:
    """Insert each unique entity into kag_graph_nodes; return count."""
    coll = arango.database.collection("kag_graph_nodes")
    inserted = 0
    for ent in entities:
        name = ent.get("name")
        if not name:
            continue
        doc = {
            "_key": f"{kb_key}__{name}",
            "kb_key": kb_key,
            "file_id": file_id,
            "name": name,
            "type": ent.get("type", "unknown"),
            "description": ent.get("description", ""),
        }
        try:
            coll.insert(doc)
            inserted += 1
        except Exception:
            # Duplicate key → already exists; that's fine.
            pass
    return inserted


def _upsert_edges(
    arango: ArangoStore,
    kb_key: str,
    file_id: str,
    relations: list[dict[str, Any]],
) -> int:
    coll = arango.database.collection("kag_graph_edges")
    inserted = 0
    for rel in relations:
        src = rel.get("from")
        dst = rel.get("to")
        label = rel.get("label", "related_to")
        if not (src and dst):
            continue
        doc = {
            "_key": f"{kb_key}__{src}__{label}__{dst}",
            "kb_key": kb_key,
            "file_id": file_id,
            "from_node": src,
            "to_node": dst,
            "label": label,
        }
        try:
            coll.insert(doc)
            inserted += 1
        except Exception:
            pass
    return inserted


@celery_app.task(name="kag.tasks.graph.graph_task", bind=True)  # type: ignore[untyped-decorator]
def graph_task(self: Any, file_id: str, kb_key: str, ontology_name: str = "") -> dict[str, Any]:
    """Extract entities + relations from the file's chunks and persist.

    ``ontology_name`` is the major-layer ontology to drive extraction
    (entity types come from its ``entity_classes``). Empty string
    falls back to a generic "thing" extraction.
    """
    log.info("graph.start", file_id=file_id, kb_key=kb_key, ontology_name=ontology_name)
    settings = get_settings()
    arango = ArangoStore()

    # 1. Pull chunks
    chunks = _load_chunks(arango, file_id)
    if not chunks:
        return {"ok": False, "error": "no chunks", "entities": 0, "relations": 0}

    # 2. Resolve entity types from ontology (if provided)
    entity_types: list[str] = []
    if ontology_name:
        onto = arango.query_one(
            """
            FOR o IN kag_ontology
              FILTER o.name == @name AND o.layer == 'major'
              RETURN o
            """,
            bind_vars={"name": ontology_name},
        )
        if onto:
            payload = onto.get("payload", {}) if isinstance(onto, dict) else {}
            entity_types = [
                c.get("name", "")
                for c in (payload.get("entity_classes", []) or [])
                if c.get("name")
            ]
    if not entity_types:
        entity_types = ["thing"]  # generic fallback

    # 3. Run extraction per chunk
    llm = LLMClient()
    total_entities = 0
    total_relations = 0
    for chunk in chunks:
        text = chunk.get("text", "")
        if not text:
            continue
        prompt = _EXTRACTION_PROMPT.format(
            entity_types=", ".join(entity_types),
            text=text,
        )
        try:
            raw = asyncio.run(
                llm.chat(
                    settings.GRAPH_MODEL,
                    [{"role": "user", "content": prompt}],
                    json_mode=True,
                    temperature=settings.LLM_TEMPERATURE_GRAPH,
                    max_tokens=settings.LLM_MAX_TOKENS_GRAPH,
                )
            )
            parsed = json.loads(raw)
        except Exception as exc:
            log.warning("graph.extract_fail", file_id=file_id, error=str(exc)[:200])
            continue

        total_entities += _upsert_nodes(arango, kb_key, file_id, parsed.get("entities", []) or [])
        total_relations += _upsert_edges(arango, kb_key, file_id, parsed.get("relations", []) or [])

    # 4. Mark file as graphed
    try:
        arango.database.collection("kag_files").update(
            {"_key": file_id, "kb_key": kb_key, "status": "graphed"}
        )
    except Exception:
        log.exception("graph.status_update_fail", file_id=file_id)

    log.info(
        "graph.ok",
        file_id=file_id,
        kb_key=kb_key,
        entities=total_entities,
        relations=total_relations,
    )
    return {
        "ok": True,
        "entities": total_entities,
        "relations": total_relations,
        "file_id": file_id,
    }
