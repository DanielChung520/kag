"""Per-format text + image extractors.

All extractors return a flat list of :class:`kag.ingestion.blocks.Block`
in source order. The pipeline then replaces ``ImageBlock`` items
with their VLM captions before chunking.

Supported formats:

- **PDF** via PyMuPDF (text per page + per-page images)
- **DOCX** via python-docx (paragraphs + inline-shape images)
- **Markdown** — header-aware split
- **Plain text** — single text block
- **Image** — single image block, MIME preserved

Anything else raises :class:`UnsupportedFileTypeError`.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

import pymupdf
import structlog
from docx import Document
from PIL import Image

from kag.ingestion.blocks import Block, ImageBlock, TextBlock

log = structlog.get_logger("kag.ingestion.extractors")


class UnsupportedFileTypeError(ValueError):
    """Raised when no extractor can handle the file's MIME / extension."""


# ── Dispatcher ────────────────────────────────────────────────────────


_PDF_EXT = {".pdf"}
_DOCX_EXT = {".docx", ".doc"}
_MD_EXT = {".md", ".markdown"}
_TXT_EXT = {".txt", ".log", ".csv"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

_PDF_MIME = {"application/pdf"}
_DOCX_MIME = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_MD_MIME = {"text/markdown"}
_TXT_MIME = {"text/plain", "text/csv"}
_IMAGE_MIME = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
}


def detect_kind(filename: str, mime: str) -> Literal["pdf", "docx", "markdown", "text", "image"]:
    ext = Path(filename).suffix.lower()
    if ext in _PDF_EXT or mime in _PDF_MIME:
        return "pdf"
    if ext in _DOCX_EXT or mime in _DOCX_MIME:
        return "docx"
    if ext in _MD_EXT or mime in _MD_MIME:
        return "markdown"
    if ext in _IMAGE_EXT or mime in _IMAGE_MIME:
        return "image"
    if ext in _TXT_EXT or mime in _TXT_MIME:
        return "text"
    raise UnsupportedFileTypeError(f"No extractor for filename={filename!r} mime={mime!r}")


def extract(data: bytes, *, filename: str, mime: str) -> list[Block]:
    """Dispatch to the right extractor based on filename/MIME."""
    kind = detect_kind(filename, mime)
    if kind == "pdf":
        return extract_pdf(data)
    if kind == "docx":
        return extract_docx(data)
    if kind == "markdown":
        return extract_markdown(data)
    if kind == "text":
        return extract_text(data)
    if kind == "image":
        return extract_image(data, mime=mime or "image/png")
    raise UnsupportedFileTypeError(kind)  # unreachable


# ── PDF ──────────────────────────────────────────────────────────────


def extract_pdf(data: bytes) -> list[Block]:
    # pymupdf's stubs mark `Document` as untyped for our usage; silence
    # the untyped-call/attr-defined noise per line.
    doc = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    try:
        blocks: list[Block] = []
        for page_index, page in enumerate(doc, start=1):  # type: ignore[var-annotated,arg-type]
            text = page.get_text()
            if text.strip():
                blocks.append(TextBlock(text=text, page_no=page_index))
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    img_bytes = doc.extract_image(xref)["image"]  # type: ignore[no-untyped-call]
                except Exception as exc:
                    log.warning(
                        "pdf.image_extract_failed",
                        page=page_index,
                        xref=xref,
                        error=str(exc),
                    )
                    continue
                blocks.append(
                    ImageBlock(
                        data=img_bytes,
                        mime="image/png",
                        page_no=page_index,
                    )
                )
        return blocks
    finally:
        doc.close()  # type: ignore[no-untyped-call]


# ── DOCX ─────────────────────────────────────────────────────────────


def extract_docx(data: bytes) -> list[Block]:
    doc = Document(io.BytesIO(data))
    blocks: list[Block] = []
    for para in doc.paragraphs:
        text = para.text
        if text.strip():
            blocks.append(
                TextBlock(
                    text=text,
                    section=para.style.name if para.style else None,
                )
            )
    # Inline images live in the run XML, not in paragraph.text. Walk
    # the document body to collect them in document order.
    for rel in doc.part.rels.values():
        target = rel.target_part
        if target is None:
            continue
        if "image" in (target.content_type or "").lower():
            blocks.append(ImageBlock(data=target.blob, mime=target.content_type or "image/png"))
    return blocks


# ── Markdown ─────────────────────────────────────────────────────────


def extract_markdown(data: bytes) -> list[Block]:
    """Header-aware split: each ``#``/``##``/... heading starts a new
    section. We do not pull images from MD in Wave 6 — they would
    require an HTML renderer to extract; a future Wave can add
    ``markdown-it-py`` or similar.
    """
    text = data.decode("utf-8", errors="replace")
    current_section = ""
    current_lines: list[str] = []
    blocks: list[Block] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if current_lines:
                blocks.append(
                    TextBlock(
                        text="\n".join(current_lines).strip(),
                        section=current_section or None,
                    )
                )
                current_lines = []
            current_section = line.lstrip("#").strip()
        else:
            current_lines.append(line)
    if current_lines:
        blocks.append(
            TextBlock(
                text="\n".join(current_lines).strip(),
                section=current_section or None,
            )
        )
    return [b for b in blocks if isinstance(b, TextBlock) and b.text.strip()]


# ── Plain text ──────────────────────────────────────────────────────


def extract_text(data: bytes) -> list[Block]:
    return [TextBlock(text=data.decode("utf-8", errors="replace"))]


# ── Image ────────────────────────────────────────────────────────────


_MAX_IMAGE_DIM = 2048


def extract_image(data: bytes, *, mime: str) -> list[Block]:
    """Preprocess the image (Pillow): downscale huge images, strip
    metadata. The pipeline later passes the bytes to the VLM.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if max(img.size) > _MAX_IMAGE_DIM:
                img.thumbnail((_MAX_IMAGE_DIM, _MAX_IMAGE_DIM))
            buf = io.BytesIO()
            fmt = img.format or "PNG"
            img.save(buf, format=fmt)
            normalized = buf.getvalue()
            normalized_mime = mime or f"image/{fmt.lower()}"
    except Exception as exc:
        log.warning("image.preprocess_failed", error=str(exc))
        normalized = data
        normalized_mime = mime
    return [ImageBlock(data=normalized, mime=normalized_mime)]
