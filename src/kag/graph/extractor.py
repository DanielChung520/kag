"""Graph extraction + dedup.

:func:`extract_entities_relations` runs the LLM over a chunk and
returns a normalized ``(entities, relations)`` tuple with deduped
keys:

- entity ``_key`` in ``kag_graph_nodes`` = ``{kb_key}__{name}``
  (lowercased + whitespace-stripped)
- edge ``_key`` in ``kag_graph_edges`` = ``{kb_key}__{from}__{label}__{to}``
  (same normalization)

The same name across two files in the same KB always lands in
the same node document; subsequent calls update the document
with the latest description / type instead of duplicating it. Same
for relations: re-extracting ``Machine produces Product`` from a
later chunk is a no-op.

Conflict resolution (task 40): on a same-key collision we keep
the existing document's ``type``/``description``; if the new
chunk supplies a *different* type or a longer description we
prefer the new one. Relations are identical and always skipped
on the second insert.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from kag.db.arango import ArangoStore
from kag.graph.prompt import build_extraction_prompt, load_ontology_payload
from kag.llm.client import LLMClient

log = structlog.get_logger("kag.extractor")

_NAME_NORMALIZE = re.compile(r"\s+")


def _norm(name: str) -> str:
    return _NAME_NORMALIZE.sub("_", name.strip()).lower()


def _entity_key(kb_key: str, name: str) -> str:
    return f"{kb_key}__{_norm(name)}"


def _edge_key(kb_key: str, src: str, dst: str, label: str) -> str:
    return f"{kb_key}__{_norm(src)}__{_norm(label)}__{_norm(dst)}"


def _merge_entity(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Conflict resolution: prefer the longer description / non-empty type."""
    merged = dict(existing)
    new_desc = (new.get("description") or "").strip()
    old_desc = (existing.get("description") or "").strip()
    if len(new_desc) > len(old_desc):
        merged["description"] = new_desc
    new_type = (new.get("type") or "").strip()
    old_type = (existing.get("type") or "").strip()
    if new_type and new_type != "unknown" and not old_type:
        merged["type"] = new_type
    return merged


def _persist_entities(
    arango: ArangoStore,
    *,
    kb_key: str,
    file_id: str,
    entities: list[dict[str, Any]],
) -> int:
    coll = arango.database.collection("kag_graph_nodes")
    inserted = 0
    for ent in entities:
        name = (ent.get("name") or "").strip()
        if not name:
            continue
        key = _entity_key(kb_key, name)
        doc = {
            "_key": key,
            "kb_key": kb_key,
            "file_id": file_id,
            "name": name,
            "type": (ent.get("type") or "unknown").strip() or "unknown",
            "description": (ent.get("description") or "").strip(),
        }
        existing = coll.get(key)
        if existing is None:
            coll.insert(doc)
            inserted += 1
        else:
            existing_dict = dict(existing) if isinstance(existing, dict) else {}
            merged = _merge_entity(existing_dict, doc)
            if merged != existing_dict:
                coll.update(merged)
    return inserted


def _persist_relations(
    arango: ArangoStore,
    *,
    kb_key: str,
    file_id: str,
    relations: list[dict[str, Any]],
) -> int:
    coll = arango.database.collection("kag_graph_edges")
    inserted = 0
    for rel in relations:
        src = (rel.get("from") or "").strip()
        dst = (rel.get("to") or "").strip()
        label = (rel.get("label") or "related_to").strip() or "related_to"
        if not (src and dst):
            continue
        key = _edge_key(kb_key, src, dst, label)
        doc = {
            "_key": key,
            "kb_key": kb_key,
            "file_id": file_id,
            "from_node": src,
            "to_node": dst,
            "label": label,
        }
        existing = coll.get(key)
        if existing is None:
            coll.insert(doc)
            inserted += 1
    return inserted


async def _extract_one_chunk(
    llm: LLMClient,
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    text: str,
    ontology: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prompt = build_extraction_prompt(text, ontology=ontology)
    raw = await llm.chat(
        model,
        [{"role": "user", "content": prompt}],
        json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    parsed = json.loads(raw)
    entities = parsed.get("entities", []) or []
    relations = parsed.get("relations", []) or []
    return entities, relations


def extract_entities_relations(
    *,
    arango: ArangoStore,
    llm: LLMClient,
    model: str,
    temperature: float,
    max_tokens: int,
    kb_key: str,
    file_id: str,
    chunks: list[dict[str, Any]],
    ontology_name: str = "",
) -> dict[str, int]:
    """Run extraction over all chunks and persist deduped nodes/edges.

    Returns ``{"entities": N, "relations": M, "chunks": L}`` with the
    number of new (non-duplicate) entities and relations persisted.
    """
    import asyncio

    ontology_payload = load_ontology_payload(arango.query_one, ontology_name)
    total_entities = 0
    total_relations = 0
    for chunk in chunks:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        try:
            entities, relations = asyncio.run(
                _extract_one_chunk(
                    llm,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    text=text,
                    ontology=ontology_payload,
                )
            )
        except Exception as exc:
            log.warning(
                "extractor.chunk_failed",
                file_id=file_id,
                error=str(exc)[:200],
            )
            continue
        total_entities += _persist_entities(
            arango, kb_key=kb_key, file_id=file_id, entities=entities
        )
        total_relations += _persist_relations(
            arango, kb_key=kb_key, file_id=file_id, relations=relations
        )
    log.info(
        "extractor.done",
        kb_key=kb_key,
        file_id=file_id,
        chunks=len(chunks),
        entities=total_entities,
        relations=total_relations,
    )
    return {
        "chunks": len(chunks),
        "entities": total_entities,
        "relations": total_relations,
    }
