"""Ingestion: parse → caption → chunk → embed-ready.

Top-level entry point: :func:`kag.ingestion.pipeline.process_file`.
"""

from __future__ import annotations

from kag.ingestion import chunker, extractors, pipeline
from kag.ingestion.blocks import Block, ImageBlock, TextBlock

__all__ = [
    "Block",
    "ImageBlock",
    "TextBlock",
    "chunker",
    "extractors",
    "pipeline",
]
