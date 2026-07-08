"""Ontology-aware prompt templates for the graph extractor.

Three pieces:

- :data:`ENTITY_TYPES_INSTRUCTION` — format snippet the prompt
  embeds so the LLM only emits entities in the major-layer
  ontology's ``entity_classes`` (with a free-form fallback if the
  ontology is empty).
- :data:`RELATION_LABEL_HINT` — small instruction block that
  biases relation labels toward the ontology's
  ``object_properties`` when those are available.
- :func:`build_extraction_prompt` — assembles the full user
  message from a chunk + an optional ontology payload.
"""

from __future__ import annotations

from typing import Any

ENTITY_TYPES_INSTRUCTION = (
    "Extract every distinct entity and every relation between them. "
    "Use ONLY the entity types listed below; if none fit, use "
    "'thing'.\n\n"
    "Entity types: {entity_types}\n\n"
    "Use a consistent short name for each entity (e.g. 'IBM', "
    "'New York', 'Qwen3-30B'). Do NOT use full sentences as names."
)

RELATION_LABEL_HINT = (
    "Use a short verb-phrase for each relation label (e.g. "
    "'produces', 'uses', 'located_in', 'reports_to'). Prefer one of "
    "the canonical labels listed below when applicable.\n\n"
    "Canonical relation labels: {relation_labels}"
)

OUTPUT_SHAPE_INSTRUCTION = """\
Output a JSON object with this exact shape:
{{
  "entities": [
    {{"name": "...", "type": "...", "description": "..."}}
  ],
  "relations": [
    {{"from": "<entity name>", "to": "<entity name>", "label": "<verb>"}}
  ]
}}
"""


def build_extraction_prompt(
    text: str,
    *,
    ontology: dict[str, Any] | None = None,
) -> str:
    """Assemble the user-facing prompt for one chunk."""
    onto = ontology or {}
    classes: list[str] = [
        c.get("name", "") for c in (onto.get("entity_classes", []) or []) if c.get("name")
    ]
    relations: list[str] = [
        p.get("name", "") for p in (onto.get("object_properties", []) or []) if p.get("name")
    ]
    parts: list[str] = [
        "You are an information-extraction assistant.",
        ENTITY_TYPES_INSTRUCTION.format(entity_types=", ".join(classes) if classes else "thing"),
    ]
    if relations:
        parts.append(RELATION_LABEL_HINT.format(relation_labels=", ".join(relations)))
    parts.append(OUTPUT_SHAPE_INSTRUCTION)
    parts.append("TEXT:\n" + text)
    return "\n\n".join(parts)


def load_ontology_payload(arango_query_fn: Any, ontology_name: str) -> dict[str, Any] | None:
    """Look up the major-layer ontology named ``ontology_name`` and
    return its raw ``payload`` dict (or ``None`` if not found).
    """
    if not ontology_name:
        return None
    onto = arango_query_fn(
        """
        FOR o IN kag_ontology
          FILTER o.name == @name AND o.layer == 'major'
          RETURN o
        """,
        bind_vars={"name": ontology_name},
    )
    if not onto:
        return None
    row = onto[0] if isinstance(onto, list) else onto
    if not isinstance(row, dict):
        return None
    return row.get("payload")
