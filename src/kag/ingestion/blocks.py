"""Parsed-content blocks returned by every extractor.

A ``Block`` is one of:
- ``TextBlock`` — a run of plain text with optional location metadata
- ``ImageBlock`` — raw image bytes with location metadata; the
  pipeline replaces this with a ``TextBlock`` (the VLM caption)
  before chunking

The union is discriminated by ``kind``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

BlockKind = Literal["text", "image"]


class _BlockBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_no: int | None = None
    section: str | None = None


class TextBlock(_BlockBase):
    kind: Literal["text"] = "text"
    text: str


class ImageBlock(_BlockBase):
    kind: Literal["image"] = "image"
    data: bytes
    mime: str
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1


Block = Annotated[
    TextBlock | ImageBlock,
    Field(discriminator="kind"),
]
