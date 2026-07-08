"""Boundary check: every hybrid call must be authorized AND scoped.

A query that bypasses these checks would either return data from
another KB or ignore lifecycle status (a deleted KB shouldn't
serve results). The check is a single fast ArangoDB lookup; we
do it once at the start of the pipeline so failed checks bail
out before any embedding / graph traversal happens.
"""

from __future__ import annotations

import structlog

from kag.db.arango import ArangoStore
from kag.models import LifecycleStatus

log = structlog.get_logger("kag.hybrid.boundary")


class BoundaryViolationError(Exception):
    """Raised when a query would cross a KB / API-key / ontology boundary."""


def check(
    *,
    arango: ArangoStore,
    kb_key: str,
    kb_ontology_version: int | None = None,
) -> None:
    """Validate the call against persisted KB state.

    Args:
        arango: open ArangoStore
        kb_key: KB identifier from the request
        kb_ontology_version: if set, must equal the persisted
            ``ontology_version`` on the KB row

    Raises:
        BoundaryViolationError: if the KB is missing, deleted,
            deprecated, or its ontology version has drifted.
    """
    row = arango.query_one(
        "FOR k IN kag_knowledge_bases FILTER k._key == @kb RETURN k",
        bind_vars={"kb": kb_key},
    )
    if row is None:
        raise BoundaryViolationError(f"KB {kb_key!r} not found")
    status = row.get("status", LifecycleStatus.ACTIVE)
    if status in {LifecycleStatus.DELETED, LifecycleStatus.DEPRECATED.value}:
        raise BoundaryViolationError(f"KB {kb_key!r} is {status!r}; hybrid queries are not served")
    if kb_ontology_version is not None and row.get("ontology_version") != kb_ontology_version:
        raise BoundaryViolationError(
            f"KB {kb_key!r} ontology version {row.get('ontology_version')} "
            f"!= requested {kb_ontology_version}"
        )
    log.debug(
        "boundary.ok",
        kb_key=kb_key,
        status=status,
        version=row.get("ontology_version"),
    )
