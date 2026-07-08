"""Paragraph-aware sliding-window chunker with tiktoken token counting.

Strategy:

1. Split each :class:`TextBlock` into paragraphs on blank lines.
2. Greedy-pack paragraphs into chunks up to ``size`` tokens
   (a single paragraph that exceeds ``size`` becomes its own chunk
   rather than being split).
3. Add ``overlap`` tokens of context from the previous chunk to
   each new chunk (the overlap is the tail of the prior chunk's
   paragraphs, re-joined with a blank line).
4. Yield each chunk with its source metadata (page_no, section).

Token counting uses the ``cl100k_base`` encoding (the same one
``bge-m3`` and ``qwen`` tokenizers roughly align with). Switch to a
per-model encoding in Wave 7 if needed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog
import tiktoken

from kag.ingestion.blocks import Block, TextBlock

log = structlog.get_logger("kag.ingestion.chunker")

_ENCODING = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _pack(
    paragraphs: list[str],
    *,
    size: int,
    overlap: int,
) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for para in paragraphs:
        para_tokens = _count_tokens(para)
        if cur and cur_tokens + para_tokens > size:
            chunks.append("\n\n".join(cur))
            # Build overlap from the tail of the current chunk
            if overlap > 0:
                tail: list[str] = []
                tail_tokens = 0
                for p in reversed(cur):
                    t = _count_tokens(p)
                    if tail_tokens + t > overlap:
                        break
                    tail.append(p)
                    tail_tokens += t
                cur = list(reversed(tail))
                cur_tokens = sum(_count_tokens(p) for p in cur)
            else:
                cur = []
                cur_tokens = 0
        if para_tokens > size:
            # Single huge paragraph: emit on its own, drop the overlap
            if cur:
                chunks.append("\n\n".join(cur))
                cur = []
                cur_tokens = 0
            chunks.append(para)
            continue
        cur.append(para)
        cur_tokens += para_tokens
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def chunk_blocks(
    blocks: Iterable[Block],
    *,
    size: int = 512,
    overlap: int = 64,
) -> list[dict[str, Any]]:
    """Return a list of ``{text, page_no, section}`` dicts ready to embed.

    Each input :class:`TextBlock` is paragraph-chunked; output chunk
    count depends on block sizes. Empty blocks are skipped.
    """
    if size <= 0:
        raise ValueError("size must be > 0")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be in [0, size)")

    out: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, TextBlock):
            continue
        if not block.text.strip():
            continue
        paragraphs = _paragraphs(block.text)
        if not paragraphs:
            continue
        for piece in _pack(paragraphs, size=size, overlap=overlap):
            if not piece.strip():
                continue
            out.append(
                {
                    "text": piece,
                    "page_no": block.page_no,
                    "section": block.section,
                }
            )
    log.info("chunk.done", input_blocks=sum(1 for _ in blocks), output_chunks=len(out))
    return out
