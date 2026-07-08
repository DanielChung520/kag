"""Graph extraction subsystem: prompt + LLM extractor + entity/relation dedup."""

from __future__ import annotations

from kag.graph.extractor import extract_entities_relations
from kag.graph.prompt import build_extraction_prompt

__all__ = ["build_extraction_prompt", "extract_entities_relations"]
