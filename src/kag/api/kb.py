"""KB CRUD endpoints.

| Method | Path | Auth |
|---|---|---|
| POST   | /api/v1/knowledge-bases | Admin |
| GET    | /api/v1/knowledge-bases | Admin |
| GET    | /api/v1/knowledge-bases/{kb_key} | Admin OR matching KB key |
| PATCH  | /api/v1/knowledge-bases/{kb_key} | Admin |
| DELETE | /api/v1/knowledge-bases/{kb_key} | Admin |

The raw API key is returned exactly once — in the response body of
``POST /knowledge-bases``. Subsequent reads expose only metadata.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, Field

from kag.auth.api_keys import KEY_PREFIX, generate_api_key, hash_key
from kag.auth.dependencies import require_admin
from kag.config import get_settings
from kag.models import APIKey, KnowledgeBase
from kag.store.kb import get_kb_store

log = structlog.get_logger("kag.api.kb")
router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge-bases"])


class KBCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    ontology_major_key: str = Field(min_length=1, max_length=200)
    ontology_version: int = Field(default=1, ge=1)


class KBCreateResponse(BaseModel):
    """Response for ``POST /knowledge-bases``; ``api_key`` is shown ONCE."""

    kb_key: str
    name: str
    description: str
    ontology_major_key: str
    ontology_version: int
    created_at: datetime
    api_key: str
    api_key_hash: str


class KBDetail(BaseModel):
    """KB view used by GET / PATCH / list — never includes the raw key."""

    kb_key: str
    name: str
    description: str
    ontology_major_key: str
    ontology_version: int
    created_at: datetime
    updated_at: datetime
    file_count: int
    status: str
    api_key_hash: str


class KBUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class KBListResponse(BaseModel):
    knowledge_bases: list[KBDetail]


def _to_detail(kb: KnowledgeBase) -> KBDetail:
    return KBDetail(
        kb_key=kb.kb_key,
        name=kb.name,
        description=kb.description,
        ontology_major_key=kb.ontology_major_key,
        ontology_version=kb.ontology_version,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        file_count=kb.file_count,
        status=kb.status,
        api_key_hash=kb.api_key_hash,
    )


def _is_admin(authorization: str | None) -> bool:
    """Constant-time check; True iff the Bearer token matches admin."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.removeprefix("Bearer ").strip()
    expected = get_settings().KAG_ADMIN_TOKEN
    return secrets.compare_digest(token, expected)


def _caller_kb_key(x_kag_api_key: str | None) -> str | None:
    """Resolve the caller's KB key from the X-KAG-API-Key header, or None.

    Looks the key up in the KB store: the APIKey record holds the
    bound ``kb_key``; that's the one we return. ``None`` is returned
    for malformed keys, unknown keys, or revoked keys.
    """
    if not x_kag_api_key or not x_kag_api_key.startswith(KEY_PREFIX):
        return None
    record = get_kb_store().find_api_key(hash_key(x_kag_api_key))
    if record is None or record.revoked:
        return None
    return record.kb_key


@router.post(
    "",
    response_model=KBCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_kb(
    body: KBCreateRequest,
    _: None = Depends(require_admin),
) -> KBCreateResponse:
    raw_key = generate_api_key()
    key_hash = hash_key(raw_key)
    now = datetime.now(UTC)

    kb = KnowledgeBase(
        name=body.name,
        description=body.description,
        ontology_major_key=body.ontology_major_key,
        ontology_version=body.ontology_version,
        created_at=now,
        updated_at=now,
        api_key_hash=key_hash,
    )
    api_key_record = APIKey(key_hash=key_hash, kb_key=kb.kb_key)

    store = get_kb_store()
    store.create(kb)
    store.add_api_key(api_key_record)

    log.info("kb.created", kb_key=kb.kb_key, name=kb.name)

    return KBCreateResponse(
        kb_key=kb.kb_key,
        name=kb.name,
        description=kb.description,
        ontology_major_key=kb.ontology_major_key,
        ontology_version=kb.ontology_version,
        created_at=kb.created_at,
        api_key=raw_key,
        api_key_hash=key_hash,
    )


@router.get("", response_model=KBListResponse)
async def list_kbs(_: None = Depends(require_admin)) -> KBListResponse:
    store = get_kb_store()
    return KBListResponse(knowledge_bases=[_to_detail(k) for k in store.list()])


@router.get("/{kb_key}", response_model=KBDetail)
async def get_kb(
    kb_key: str = Path(..., min_length=1),
    authorization: str | None = Header(default=None),
    x_kag_api_key: str | None = Header(default=None, alias="X-KAG-API-Key"),
) -> KBDetail:
    """Admin OR a per-KB key whose bound kb_key matches the path."""
    is_admin = _is_admin(authorization)
    caller_kb = _caller_kb_key(x_kag_api_key)
    if not is_admin and caller_kb != kb_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    store = get_kb_store()
    kb = store.get(kb_key)
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    return _to_detail(kb)


@router.patch("/{kb_key}", response_model=KBDetail)
async def update_kb(
    body: KBUpdateRequest,
    kb_key: str = Path(..., min_length=1),
    _: None = Depends(require_admin),
) -> KBDetail:
    store = get_kb_store()
    updated = store.update(kb_key, name=body.name, description=body.description)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    log.info("kb.updated", kb_key=kb_key)
    return _to_detail(updated)


@router.delete("/{kb_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    kb_key: str = Path(..., min_length=1),
    _: None = Depends(require_admin),
) -> None:
    store = get_kb_store()
    if not store.delete(kb_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    log.info("kb.deleted", kb_key=kb_key)
