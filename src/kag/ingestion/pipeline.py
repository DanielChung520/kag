"""Ingestion pipeline: parse → caption → chunk → embed-ready.

`process_file` is the single entry point the vectorize task calls.
It dispatches to the right extractor, asks the VLM to caption any
images, then hands the resulting text blocks to the chunker.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from kag.config import get_settings
from kag.ingestion import chunker, extractors
from kag.ingestion.blocks import Block, ImageBlock, TextBlock
from kag.llm.client import LLMClient

log = structlog.get_logger("kag.ingestion.pipeline")

_VLM_PROMPT = (
    "Describe this image in detail. Focus on: what is shown, any text "
    "visible (transcribe it), and any data or relationships implied. "
    "Be specific and concise."
)


def _caption_image(llm: LLMClient, model: str, block: ImageBlock) -> TextBlock:
    """Synchronously call the VLM (the LLM client is async)."""
    caption = asyncio.run(llm.vl_caption(model, block.data, _VLM_PROMPT))
    section = block.section or "image"
    if block.page_no is not None:
        section = f"{section} (page {block.page_no})"
    return TextBlock(text=caption, page_no=block.page_no, section=section)


def process_file(
    data: bytes,
    *,
    filename: str,
    mime: str,
    vlm_model: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[dict[str, Any]]:
    """Parse + caption + chunk; return a list of embed-ready chunks.

    Each chunk is a ``{text, page_no, section}`` dict. Chunks that
    come from an image carry the image's page_no / section so the
    caller can back-reference them.
    """
    settings = get_settings()
    vlm = vlm_model or settings.VLM_MODEL
    size = chunk_size or settings.KAG_VECTOR_CHUNK_SIZE
    overlap = chunk_overlap or settings.KAG_VECTOR_CHUNK_OVERLAP

    blocks: list[Block] = extractors.extract(data, filename=filename, mime=mime)
    log.info("pipeline.extracted", filename=filename, blocks=len(blocks))

    # Caption images in place
    llm_client: LLMClient | None = None
    text_blocks: list[TextBlock] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            text_blocks.append(block)
            continue
        # ImageBlock
        if llm_client is None:
            llm_client = LLMClient()
        try:
            text_blocks.append(_caption_image(llm_client, vlm, block))
        except Exception as exc:
            log.warning(
                "pipeline.caption_failed",
                filename=filename,
                error=str(exc)[:200],
            )
            # Fall back to a placeholder so downstream indexing still
            # knows this file had an image it couldn't caption.
            text_blocks.append(
                TextBlock(
                    text=f"[image: {block.mime}, caption unavailable: {exc}]",
                    page_no=block.page_no,
                    section=block.section,
                )
            )

    chunks = chunker.chunk_blocks(text_blocks, size=size, overlap=overlap)
    log.info(
        "pipeline.chunked",
        filename=filename,
        text_blocks=len(text_blocks),
        chunks=len(chunks),
    )
    return chunks
