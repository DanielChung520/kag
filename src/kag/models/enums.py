"""Shared enums for kag domain models.

`StrEnum` ensures values are JSON-serializable strings out of the box
(no `Enum.value` needed in API responses).
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleStatus(StrEnum):
    """Resource lifecycle (used by KB and Ontology)."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DELETED = "deleted"
