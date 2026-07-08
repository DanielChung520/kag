"""SeaweedFS S3-compatible object storage adapter.

Wraps boto3's S3 client for the ``kag`` bucket with namespace-safe ``kag/``
key prefix enforcement.  All file keys are built via :meth:`SeaweedStore.key_for`
and are guaranteed to start with ``kag/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]  # boto3 lacks PEP 561 stubs
import botocore.exceptions  # type: ignore[import-untyped]  # botocore lacks PEP 561 stubs
import structlog

from kag.config import get_settings

logger = structlog.get_logger(__name__)


class SeaweedStore:
    """Typed wrapper around a SeaweedFS S3-compatible bucket.

    Connects lazily — the underlying ``boto3.client("s3", …)`` is created
    on first use, not at instantiation.

    All public file-access methods raise :class:`ValueError` if *key* or
    *prefix* does not start with ``kag/``.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._endpoint_url: str = settings.SEAWEED_URL
        self._bucket_name: str = settings.SEAWEED_BUCKET
        self._access_key: str = settings.SEAWEED_ACCESS_KEY
        self._secret_key: str = settings.SEAWEED_SECRET_KEY
        self._default_ttl: int = settings.KAG_DOWNLOAD_URL_TTL
        self._client: Any = None

    # ── properties ──────────────────────────────────────────────────────

    @property
    def bucket(self) -> str:
        """The S3 bucket name (e.g. ``kag``)."""
        return self._bucket_name

    # ── key namespace ────────────────────────────────────────────────────

    @classmethod
    def key_for(cls, kb_key: str, file_id: str, filename: str) -> str:
        """Build a namespaced object key.

        Returns ``kag/{kb_key}/{file_id}/original/{filename.name}``.
        This is the **only** place that constructs file keys.
        """
        return f"kag/{kb_key}/{file_id}/original/{Path(filename).name}"

    # ── internal helpers ────────────────────────────────────────────────

    def _get_client(self) -> Any:
        """Return the cached boto3 S3 client, creating it on first call."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint_url,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name="us-east-1",
            )
        return self._client

    @staticmethod
    def _validate_key(key: str) -> None:
        """Ensure *key* starts with ``kag/``; raise ValueError otherwise."""
        if not key.startswith("kag/"):
            raise ValueError(f"Key must start with 'kag/', got {key!r}")

    @staticmethod
    def _validate_prefix(prefix: str) -> None:
        """Ensure *prefix* starts with ``kag/``; raise ValueError otherwise."""
        if not prefix.startswith("kag/"):
            raise ValueError(f"Prefix must start with 'kag/', got {prefix!r}")

    # ── public API ───────────────────────────────────────────────────────

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist.

        Idempotent — safe to call repeatedly. Uses ``head_bucket`` first
        so the common no-op path doesn't hit SeaweedFS's quirky
        ``create_bucket`` response (some versions return a JSON body
        boto3's XML parser cannot decode).
        """
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self._bucket_name)
            logger.debug("bucket.already_exists", bucket=self._bucket_name)
            return
        except botocore.exceptions.ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404:
                raise

        try:
            client.create_bucket(Bucket=self._bucket_name)
            logger.info("bucket.created", bucket=self._bucket_name)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                logger.debug("bucket.already_exists", bucket=self._bucket_name)
                return
            raise
        except botocore.exceptions.ResponseParserError:
            # SeaweedFS quirk: returns a JSON body for an existing-bucket
            # create call. The bucket exists either way; fall through to
            # head_bucket to confirm.
            try:
                client.head_bucket(Bucket=self._bucket_name)
            except botocore.exceptions.ClientError:
                raise
            logger.debug("bucket.already_exists", bucket=self._bucket_name)

    def upload_file(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        """Upload *data* as the object at *key*.

        Args:
            key: Object key (must start with ``kag/``).
            data: Raw bytes to upload.
            content_type: Optional MIME type (e.g. ``application/pdf``).
        """
        self._validate_key(key)
        client = self._get_client()
        if content_type is not None:
            client.put_object(
                Bucket=self._bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        else:
            client.put_object(Bucket=self._bucket_name, Key=key, Body=data)
        logger.debug("file.uploaded", key=key, size=len(data))

    def download_file(self, key: str) -> bytes:
        """Download the object at *key* and return its bytes.

        Raises:
            botocore.exceptions.ClientError: If the key does not exist
                (``NoSuchKey``) or another S3 error occurs.
        """
        self._validate_key(key)
        client = self._get_client()
        response = client.get_object(Bucket=self._bucket_name, Key=key)
        body: bytes = response["Body"].read()
        logger.debug("file.downloaded", key=key, size=len(body))
        return body

    def delete_file(self, key: str) -> None:
        """Delete the object at *key*.

        Does **not** raise on a missing key — S3 ``delete_object`` is
        idempotent by design.
        """
        self._validate_key(key)
        client = self._get_client()
        client.delete_object(Bucket=self._bucket_name, Key=key)
        logger.debug("file.deleted", key=key)

    def list_files(self, prefix: str) -> Iterator[str]:
        """Yield all object keys under *prefix*.

        Pagination is handled transparently via the S3 paginator.

        Args:
            prefix: Key prefix (must start with ``kag/``).

        Yields:
            Full object keys matching the prefix.
        """
        self._validate_prefix(prefix)
        client = self._get_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield str(obj["Key"])

    def presigned_url(
        self,
        key: str,
        *,
        expires: int | None = None,
    ) -> str:
        """Generate a time-limited GET URL for *key*.

        Args:
            key: Object key (must start with ``kag/``).
            expires: TTL in seconds.  Defaults to
                ``KAG_DOWNLOAD_URL_TTL`` from settings (3600).

        Returns:
            A pre-signed URL that can be used to download the object
            without additional authentication.
        """
        self._validate_key(key)
        client = self._get_client()
        ttl = expires if expires is not None else self._default_ttl
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_name, "Key": key},
            ExpiresIn=ttl,
        )
        logger.debug("presigned_url.generated", key=key, ttl=ttl)
        return url
