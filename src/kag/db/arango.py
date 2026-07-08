"""ArangoDB adapter with lazy connection, namespaced collections, and AQL helpers.

Uses ``python-arango`` (sync). All kag collections are prefixed with ``kag_``
to namespace alongside aibox-th in the same database.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import structlog
from arango import ArangoClient  # type: ignore[attr-defined]  # python-arango has no py.typed stub
from arango.database import StandardDatabase

from kag.config import get_settings

log = structlog.get_logger(__name__)

# ── Collection schema definitions ──────────────────────────────────────
# Each entry maps a collection name to a list of index definitions.
# Indexes defined here are created/verified by ensure_collections().
# The implicit _key index is always present and omitted from these lists.

_COLLECTION_DEFS: dict[str, list[dict[str, Any]]] = {
    "kag_ontology": [
        {"type": "hash", "fields": ["created_at"]},
    ],
    "kag_ontology_version": [
        {"type": "hash", "fields": ["created_at"]},
        {"type": "skiplist", "fields": ["version"]},
    ],
    "kag_knowledge_bases": [
        {"type": "hash", "fields": ["created_at"]},
        {"type": "hash", "fields": ["status"]},
    ],
    "kag_files": [
        {"type": "hash", "fields": ["kb_key"]},
        {"type": "hash", "fields": ["status"]},
        {"type": "skiplist", "fields": ["uploaded_at"]},
    ],
    "kag_jobs": [
        {"type": "hash", "fields": ["kb_key"]},
        {"type": "hash", "fields": ["status"]},
        {"type": "skiplist", "fields": ["started_at"]},
    ],
    "kag_api_keys": [
        {"type": "hash", "fields": ["kb_key"]},
        {"type": "hash", "fields": ["revoked"]},
    ],
    "kag_chunks": [
        {"type": "hash", "fields": ["kb_key"]},
        {"type": "hash", "fields": ["file_id"]},
        {"type": "skiplist", "fields": ["chunk_index"]},
    ],
    "kag_graph_nodes": [
        {"type": "hash", "fields": ["kb_key"]},
        {"type": "skiplist", "fields": ["name"]},
    ],
    "kag_graph_edges": [
        {"type": "hash", "fields": ["kb_key"]},
        {"type": "hash", "fields": ["from_node"]},
        {"type": "hash", "fields": ["to_node"]},
    ],
}


class ArangoStore:
    """Typed wrapper around an ArangoDB database connection.

    Connection is established lazily on first use (not at construction
    time) so importing ``ArangoStore`` has no side effects. Call
    :meth:`ensure_collections` during startup to create the six
    ``kag_``-prefixed collections with their indexes.

    Usage::

        store = ArangoStore()
        store.ensure_collections()

        doc = store.query_one("FOR doc IN kag_ontology FILTER ... RETURN doc")
        docs = store.query_all("FOR doc IN kag_files RETURN doc")
        for doc in store.query_iter("FOR doc IN kag_jobs RETURN doc"):
            ...
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._url: str = settings.ARANGO_URL
        self._db_name: str = settings.ARANGO_DB
        self._username: str = settings.ARANGO_USER
        self._password: str = settings.ARANGO_PASSWORD
        self._client: ArangoClient | None = None
        self._db: StandardDatabase | None = None

    # ── Connection management ──────────────────────────────────────────

    def _connect(self) -> StandardDatabase:
        """Establish (or return) a connected :class:`StandardDatabase`.

        Retries up to 3 times with exponential backoff (1s, 2s)
        before raising the underlying exception.
        """
        if self._db is not None:
            return self._db

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                client = ArangoClient(hosts=self._url)
                db = client.db(
                    name=self._db_name,
                    username=self._username,
                    password=self._password,
                )
                # Force a lightweight round-trip to verify connectivity.
                db.properties()
                self._client = client
                self._db = db
                return db
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    delay = 2**attempt
                    log.warning(
                        "arango.connect_attempt_failed",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    time.sleep(delay)

        log.error("arango.connect_failed", error=str(last_exc))
        raise last_exc  # type: ignore[misc]  # last_exc always set after loop

    def close(self) -> None:
        """Release the underlying ArangoDB client connection."""
        self._db = None
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def database(self) -> StandardDatabase:
        """The connected :class:`StandardDatabase` (lazy-connects)."""
        return self._connect()

    # ── Namespacing helper ─────────────────────────────────────────────

    @classmethod
    def cname(cls, local: str) -> str:
        """Return a fully-namespaced collection name.

        All kag collections live under the ``kag_`` prefix to avoid
        collisions with aibox-th collections in the same database.

        Args:
            local: The local name (e.g. ``"ontology"``).

        Returns:
            ``f"kag_{local}"`` (e.g. ``"kag_ontology"``).
        """
        return f"kag_{local}"

    # ── Schema management ──────────────────────────────────────────────

    def ensure_collections(self) -> None:
        """Idempotently create the six ``kag_``-prefixed collections.

        For each collection defined in ``_COLLECTION_DEFS``:
        - Creates the collection if it does not exist.
        - Creates each index if it does not already exist (matched by
          index field set).

        Safe to call repeatedly — existing collections/indexes are
        silently skipped.
        """
        db = self._connect()

        for col_name, indexes in _COLLECTION_DEFS.items():
            if db.has_collection(col_name):
                log.info("collection_exists", name=col_name)
            else:
                db.create_collection(col_name, edge=False)
                log.info("collection_created", name=col_name)

            col = db.collection(col_name)
            # Build a set of existing index field-tuples for quick lookup.
            existing_field_sets: set[frozenset[str]] = set()
            for idx in col.indexes():  # type: ignore[union-attr]  # sync → always returns list
                fields = idx.get("fields")
                if fields:
                    existing_field_sets.add(frozenset(fields))

            for idx_def in indexes:
                fields = list(idx_def["fields"])
                if frozenset(fields) in existing_field_sets:
                    continue
                col.add_index({"type": idx_def["type"], "fields": fields})
                log.info(
                    "index_created",
                    collection=col_name,
                    index_type=idx_def["type"],
                    fields=fields,
                )

    # ── AQL query helpers ──────────────────────────────────────────────

    def query_one(
        self,
        aql: str,
        *,
        bind_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute an AQL query and return the first result, or ``None``.

        Args:
            aql: The AQL query string.
            bind_vars: Optional bind parameter dictionary.

        Returns:
            The first document, or ``None`` if the result set is empty.
        """
        db = self._connect()
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        for doc in cursor:  # type: ignore[union-attr]  # sync → always Cursor
            return doc  # type: ignore[no-any-return]  # Cursor.next() returns Any
        return None

    def query_all(
        self,
        aql: str,
        *,
        bind_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an AQL query and return all results as a list.

        Args:
            aql: The AQL query string.
            bind_vars: Optional bind parameter dictionary.

        Returns:
            A list of matching documents (may be empty).
        """
        db = self._connect()
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        return list(cursor)  # type: ignore[arg-type]  # sync → always Cursor, iterable

    def query_iter(
        self,
        aql: str,
        *,
        bind_vars: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Execute an AQL query and return an iterator over results.

        Args:
            aql: The AQL query string.
            bind_vars: Optional bind parameter dictionary.

        Returns:
            An iterator yielding each matching document.
        """
        db = self._connect()
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        return iter(cursor)  # type: ignore[arg-type]  # sync → always Cursor, iterable
