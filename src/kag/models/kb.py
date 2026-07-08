"""Knowledge Base and Knowledge File models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FileStatus(StrEnum):
    """Per-file processing lifecycle."""

    PENDING = "pending"
    PROCESSING = "processing"
    VECTORIZED = "vectorized"
    GRAPHED = "graphed"
    FAILED = "failed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class KnowledgeBase(BaseModel):
    """A knowledge base: ontology-bound container for documents + graph.

    The raw API key is **never** stored here; only ``api_key_hash`` of
    the most recent active key is retained. Rotation creates a new
    :class:`APIKey` record and revokes the oldest.
    """

    model_config = ConfigDict(extra="ignore")

    kb_key: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    ontology_major_key: str
    ontology_version: int = 1
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    file_count: int = 0
    api_key_hash: str = ""
    status: str = "active"


class KnowledgeFile(BaseModel):
    """A single file uploaded to a KB; tracks its processing state."""

    model_config = ConfigDict(extra="ignore")

    file_id: str = Field(default_factory=_new_id)
    kb_key: str
    filename: str
    mime: str = "application/octet-stream"
    size_bytes: int
    status: FileStatus = FileStatus.PENDING
    error_msg: str | None = None
    seaweed_key: str
    uploaded_at: datetime = Field(default_factory=_utc_now)
    processed_at: datetime | None = None
