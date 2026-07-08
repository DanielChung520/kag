"""FastAPI dependencies for the two auth schemes.

- ``current_kb`` — for per-KB endpoints (file upload, search, pipeline
  trigger). Reads ``X-KAG-API-Key`` header, hashes it, looks up the
  bound :class:`APIKey`, then loads the owning :class:`KnowledgeBase`.
  Raises ``404`` if the key is unknown or revoked; ``401`` if the
  header is missing.

- ``require_admin`` — for KB / ontology management endpoints. Reads
  ``Authorization: Bearer <token>`` and constant-time compares to
  :attr:`Settings.KAG_ADMIN_TOKEN`. Raises ``401`` on mismatch.

Wave 3 ships the shape of these deps with format validation only.
Full ArangoDB lookup lands alongside the KB CRUD endpoints (task 16)
in a follow-up; until then, the dep derives a deterministic stub KB
from the key hash so downstream endpoints have something to work with.
"""

from __future__ import annotations

import secrets
from typing import Annotated

import structlog
from fastapi import Header, HTTPException, status

from kag.auth.api_keys import KEY_PREFIX, hash_key
from kag.config import get_settings
from kag.models import KnowledgeBase

log = structlog.get_logger("kag.auth")

API_KEY_HEADER = "X-KAG-API-Key"
MAX_KEYS_PER_KB = 5


async def current_kb(
    x_kag_api_key: Annotated[str, Header(alias=API_KEY_HEADER)],
) -> KnowledgeBase:
    """Resolve the :class:`KnowledgeBase` for the calling API key.

    The returned object exposes ``api_key_hash`` so handlers can
    correlate jobs/files back to the key that created them.

    Today this consults the in-memory KB store (Wave 3). The same
    contract carries over to the ArangoDB-backed implementation in
    a follow-up: the dep returns a :class:`KnowledgeBase`, and the
    store internals are an implementation detail.
    """
    if not x_kag_api_key.startswith(KEY_PREFIX):
        log.warning("auth.bad_key_format", prefix=x_kag_api_key[:8])
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not recognized",
        )

    from kag.store.kb import get_kb_store  # local import: avoid dep cycle

    key_hash = hash_key(x_kag_api_key)
    api_key_record = get_kb_store().find_api_key(key_hash)
    if api_key_record is None or api_key_record.revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not recognized",
        )
    kb = get_kb_store().get(api_key_record.kb_key)
    if kb is None:
        log.error("auth.kb_missing_for_key", kb_key=api_key_record.kb_key)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not recognized",
        )
    return kb


async def require_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Verify the ``Authorization: Bearer <token>`` header.

    Constant-time comparison against :attr:`Settings.KAG_ADMIN_TOKEN`.
    No return value — presence of the admin token is the only check.
    """
    expected = get_settings().KAG_ADMIN_TOKEN
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, expected):
        log.warning("auth.admin_token_mismatch", token_prefix=token[:4])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
