"""Celery job tracking model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobType(StrEnum):
    """Type of background work a job represents."""

    VECTORIZE = "vectorize"
    GRAPH_EXTRACT = "graph_extract"


class JobStatus(StrEnum):
    """Celery job lifecycle."""

    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class Job(BaseModel):
    """Tracks a single Celery task for a KB/file.

    `kb_key` is always set; `file_id` is set for per-file jobs (vectorize
    / graph_extract on a single upload) and None for whole-KB jobs.
    """

    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(default_factory=_new_id)
    type: JobType
    kb_key: str
    file_id: str | None = None
    status: JobStatus = JobStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_msg: str | None = None
    log_tail: str = ""
    created_at: datetime = Field(default_factory=_utc_now)
