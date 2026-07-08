"""Schema migration logic.

Wave 2 task 12: invoked by `kag migrate` on the CLI. Idempotently
creates all kag datastore resources so a fresh checkout can boot
against an existing ArangoDB / Qdrant / SeaweedFS / Redis stack.
"""

from __future__ import annotations

import structlog

from kag.db.arango import ArangoStore
from kag.db.seaweedfs import SeaweedStore

log = structlog.get_logger("kag.migrate")


def run_migrations() -> None:
    """Create the six kag_*-prefixed ArangoDB collections and the kag SeaweedFS bucket.

    Both steps are idempotent. Qdrant collections are per-KB and are
    created lazily by :meth:`kag.db.qdrant.QdrantStore.ensure_collection`
    on first file upload — nothing to do here.
    """
    arango = ArangoStore()
    arango.ensure_collections()
    log.info("arango.migrated")

    seaweed = SeaweedStore()
    seaweed.ensure_bucket()
    log.info("seaweedfs.migrated", bucket=seaweed.bucket)
