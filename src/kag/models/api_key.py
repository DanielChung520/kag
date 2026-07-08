"""Per-KB API key record (hash-only; the raw key is shown once at creation)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class APIKey(BaseModel):
    """An active or revoked API key bound to a single KB.

    Only ``key_hash`` (sha256 of the raw key + server pepper) is stored;
    the raw key is returned to the caller exactly once at creation and
    cannot be recovered from this record.
    """

    model_config = ConfigDict(extra="ignore")

    key_hash: str
    kb_key: str
    label: str = ""
    created_at: datetime = Field(default_factory=_utc_now)
    last_used_at: datetime | None = None
    revoked: bool = False
