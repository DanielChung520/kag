"""Qdrant vector store adapter for per-KB collections.

Every collection created by this module is namespaced with a ``kag_kb_``
prefix to avoid collisions with other services sharing the same Qdrant
instance (notably aibox-th).
"""

from __future__ import annotations

import contextlib

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    Condition,
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    ScoredPoint,
    VectorParams,
)

from kag.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class QdrantStore:
    """Typed wrapper around a sync :class:`QdrantClient` for per-KB collections.

    Collections are namespaced ``kag_kb_<kb_key>``.  The constructor accepts
    an optional pre-configured :class:`QdrantClient` or the project-wide
    :class:`Settings`.  The connection is established lazily on first use.
    """

    COLLECTION_PREFIX = "kag_kb_"

    def __init__(
        self,
        client: QdrantClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialise the store.

        Args:
            client: A pre-configured sync :class:`QdrantClient`. If omitted,
                one is created from *settings* (or the process-wide default).
            settings: Config subset containing connection details.
                Falls back to :func:`get_settings` when both *client* and
                *settings* are ``None``.
        """
        if client is not None:
            self._client = client
        else:
            s = settings if settings is not None else get_settings()
            self._client = QdrantClient(
                url=s.QDRANT_URL,
                api_key=s.QDRANT_API_KEY or None,
            )

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    @classmethod
    def collection_name(cls, kb_key: str) -> str:
        """Return the namespaced Qdrant collection name for *kb_key*.

        This is the **only** function that should build collection names in
        this codebase — never hand-craft the ``kag_kb_`` prefix elsewhere.

        Raises:
            ValueError: If *kb_key* already starts with the prefix (indicating
                a double-namespace bug).
        """
        if kb_key.startswith(cls.COLLECTION_PREFIX):
            raise ValueError(
                f"kb_key {kb_key!r} already starts with "
                f"{cls.COLLECTION_PREFIX!r}; "
                "did you accidentally pass a collection name instead of a kb_key?"
            )
        return f"{cls.COLLECTION_PREFIX}{kb_key}"

    def _validate_collection_name(self, name: str) -> None:
        """Raise :class:`ValueError` if *name* doesn't start with the kag prefix."""
        if not name.startswith(self.COLLECTION_PREFIX):
            raise ValueError(f"Collection name {name!r} must start with {self.COLLECTION_PREFIX!r}")

    # ------------------------------------------------------------------
    # Collection lifecycle
    # ------------------------------------------------------------------

    def ensure_collection(self, name: str, dim: int) -> None:
        """Idempotently create *name* with the given vector dimension.

        If the collection already exists, verifies its vector dimension
        matches *dim* — raises :class:`ValueError` on mismatch.

        After creation (or confirmation), creates ``KEYWORD`` payload indexes
        for ``kb_key``, ``file_id``, and ``doc_id``.  Index creation is
        best-effort: if an index already exists, the error is silently
        ignored.

        Raises:
            ValueError: If *name* doesn't start with ``kag_kb_`` or if the
                existing collection has a different vector dimension.
        """
        self._validate_collection_name(name)

        try:
            info = self._client.get_collection(name)
            vectors_config = info.config.params.vectors
            if vectors_config is None:
                raise ValueError(f"Collection {name!r} exists but has no vector config")
            if isinstance(vectors_config, dict):
                existing_dim = next(iter(vectors_config.values())).size
            else:
                existing_dim = vectors_config.size
            if existing_dim != dim:
                raise ValueError(
                    f"Collection {name!r} already exists with dimension "
                    f"{existing_dim}, but {dim} was requested"
                )
            logger.info("collection.already_exists", name=name, dim=dim)
        except UnexpectedResponse:
            self._client.create_collection(
                name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("collection.created", name=name, dim=dim)

        # Create payload indexes; skip silently if they already exist.
        for field in ("kb_key", "file_id", "doc_id"):
            with contextlib.suppress(UnexpectedResponse):
                self._client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def upsert_chunks(self, collection: str, points: list[PointStruct]) -> None:
        """Upsert *points* into *collection*.

        Args:
            collection: Qdrant collection name (must start with ``kag_kb_``).
            points: List of :class:`PointStruct` instances to upsert.
        """
        self._validate_collection_name(collection)
        self._client.upsert(collection, points=points, wait=True)

    def search(
        self,
        collection: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        kb_key: str | None = None,
        file_id: str | None = None,
    ) -> list[ScoredPoint]:
        """Vector similarity search.

        Args:
            collection: Qdrant collection name (must start with ``kag_kb_``).
            query_vector: Dense vector to search by.
            top_k: Number of results to return (default 10).
            kb_key: Optional kb_key filter for scoping results to one KB.
            file_id: Optional file_id filter (ANDed with ``kb_key`` when both
                are provided).

        Returns:
            List of :class:`ScoredPoint` ordered by descending score.
        """
        self._validate_collection_name(collection)

        conditions: list = []  # type: ignore[type-arg]
        if kb_key is not None:
            conditions.append(FieldCondition(key="kb_key", match=MatchValue(value=kb_key)))
        if file_id is not None:
            conditions.append(FieldCondition(key="file_id", match=MatchValue(value=file_id)))
        query_filter: Filter | None = Filter(must=conditions) if conditions else None

        result = self._client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )
        return result.points

    def delete_by_filter(
        self,
        collection: str,
        *,
        kb_key: str | None = None,
        file_id: str | None = None,
    ) -> None:
        """Delete points matching *kb_key* and/or *file_id*.

        If both filters are provided they are ANDed together. If neither is
        provided the call is a no-op (refusing to delete the entire collection
        without an explicit ``clear_collection`` method).

        Args:
            collection: Qdrant collection name (must start with ``kag_kb_``).
            kb_key: Optional kb_key filter (keyword match).
            file_id: Optional file_id filter (keyword match).
        """
        self._validate_collection_name(collection)

        if kb_key is None and file_id is None:
            logger.warning("delete_by_filter called with no filters — skipping")
            return

        conditions: list[Condition] = []
        if kb_key is not None:
            conditions.append(
                FieldCondition(key="kb_key", match=MatchValue(value=kb_key)),
            )
        if file_id is not None:
            conditions.append(
                FieldCondition(key="file_id", match=MatchValue(value=file_id)),
            )

        self._client.delete(
            collection_name=collection,
            points_selector=Filter(must=conditions),
            wait=True,
        )
