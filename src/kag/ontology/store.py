"""In-memory ontology store.

Tracks two collections (mirroring the ArangoDB layout):

- ``_latest``: ``kag_ontology`` — the latest version per ``(layer, name)``.
- ``_history``: ``kag_ontology_version`` — every version, keyed by
  ``f"{name}__v{version}"``. Immutable; old rows are never overwritten.

Same DB-swap plan as :class:`kag.store.kb.KBStore` and
:class:`kag.store.kb.FileStore`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from kag.models import LifecycleStatus, Ontology, OntologyLayer
from kag.ontology.schema import validate_payload


class OntologyStore:
    """In-memory store with versioning."""

    def __init__(self) -> None:
        self._latest: dict[str, Ontology] = {}
        self._history: dict[str, Ontology] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _version_key(name: str, version: int) -> str:
        return f"{name}__v{version}"

    @staticmethod
    def _latest_key(layer: str, name: str) -> str:
        return f"{layer}:{name}"

    # ── Writes ─────────────────────────────────────────────────────────

    def put_new_version(
        self,
        *,
        name: str,
        layer: str,
        payload: dict[str, Any],
    ) -> Ontology:
        """Validate ``payload``, then store a new version of the ontology.

        If an ontology with the same ``(layer, name)`` already exists,
        the new version is ``existing.version + 1``; otherwise it
        starts at 1. The previous version is kept in the immutable
        history collection.
        """
        validated = validate_payload(payload)
        if validated.name != name or str(validated.layer) != layer:
            raise ValueError(
                f"path key {name!r} (layer {layer!r}) does not match "
                f"payload (name {validated.name!r}, layer {validated.layer!r})"
            )
        with self._lock:
            latest_key = self._latest_key(layer, name)
            existing = self._latest.get(latest_key)
            new_version = 1 if existing is None else existing.version + 1

            now = datetime.now(UTC)
            payload_dict = validated.model_dump()
            ontology = Ontology(
                key=name,
                layer=OntologyLayer(str(validated.layer)),
                name=validated.name,
                version=new_version,
                parent_version=existing.version if existing else None,
                status=LifecycleStatus.ACTIVE,
                payload=payload_dict,
                created_at=now,
            )
            self._history[self._version_key(name, new_version)] = ontology
            self._latest[latest_key] = ontology
            return ontology

    def soft_delete(self, layer: str, name: str) -> Ontology | None:
        """Mark the latest version as deprecated; old versions remain."""
        with self._lock:
            latest_key = self._latest_key(layer, name)
            existing = self._latest.get(latest_key)
            if existing is None:
                return None
            deprecated = existing.model_copy(update={"status": LifecycleStatus.DEPRECATED})
            self._latest[latest_key] = deprecated
            self._history[self._version_key(name, deprecated.version)] = deprecated
            return deprecated

    # ── Reads ──────────────────────────────────────────────────────────

    def get_latest(self, layer: str, name: str) -> Ontology | None:
        with self._lock:
            return self._latest.get(self._latest_key(layer, name))

    def get_version(self, layer: str, name: str, version: int) -> Ontology | None:
        with self._lock:
            return self._history.get(self._version_key(name, version))

    def list_latest(self) -> list[Ontology]:
        with self._lock:
            return sorted(self._latest.values(), key=lambda o: (o.layer, o.name))

    def list_versions(self, name: str) -> list[Ontology]:
        with self._lock:
            return sorted(
                (o for k, o in self._history.items() if k.startswith(f"{name}__v")),
                key=lambda o: o.version,
            )

    def versions_iter(self) -> Iterable[Ontology]:
        with self._lock:
            return list(self._history.values())


_store: OntologyStore | None = None


def get_ontology_store() -> OntologyStore:
    """Return the process-wide :class:`OntologyStore` instance."""
    global _store
    if _store is None:
        _store = OntologyStore()
    return _store
