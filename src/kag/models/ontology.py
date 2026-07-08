"""Ontology model — Basic / Domain / Major three-layer hierarchy."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from kag.models.enums import LifecycleStatus


class OntologyLayer(StrEnum):
    """Three-layer ontology hierarchy.

    `BASIC` is the universal 5W1H metadata layer; `DOMAIN` defines
    entity classes + object properties for a domain; `MAJOR` is a
    domain-specific specialization (inherits from a DOMAIN).
    """

    BASIC = "basic"
    DOMAIN = "domain"
    MAJOR = "major"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Ontology(BaseModel):
    """An ontology version.

    Each PUT creates a new version row in ``kag_ontology_version``; the
    ``kag_ontology`` collection always holds the latest version per key.
    """

    model_config = ConfigDict(extra="ignore")

    key: str
    layer: OntologyLayer
    name: str
    version: int = 1
    parent_version: int | None = None
    status: LifecycleStatus = LifecycleStatus.ACTIVE
    payload: dict[str, object]
    created_at: datetime = Field(default_factory=_utc_now)
