"""File upload / listing endpoints.

| Method | Path | Auth |
|---|---|---|
| POST | /api/v1/knowledge-bases/{kb_key}/files | KB Key (multipart or path) |
| GET  | /api/v1/knowledge-bases/{kb_key}/files | KB Key |

Upload modes:

- **multipart**: ``POST`` with ``Content-Type: multipart/form-data`` and
  a ``file`` field. The bytes are streamed from the request.
- **path**: ``POST`` with ``Content-Type: application/json`` and body
  ``{"path": "/abs/path/to/file"}``. The service reads the file
  server-side. Path mode is gated by ``KAG_FILE_PATH_ALLOWLIST``;
  if non-empty, the path must start with one of the comma-separated
  allowed prefixes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi import (
    Path as PathParam,
)
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from kag.auth.dependencies import current_kb
from kag.config import get_settings
from kag.db.seaweedfs import SeaweedStore
from kag.models import KnowledgeBase, KnowledgeFile
from kag.store.kb import get_file_store, get_kb_store

log = structlog.get_logger("kag.api.files")
router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["files"])


class FileUploadResponse(BaseModel):
    file_id: str
    kb_key: str
    filename: str
    mime: str
    size_bytes: int
    seaweed_key: str
    status: str
    uploaded_at: str


class FileListResponse(BaseModel):
    files: list[FileUploadResponse]


class PathUploadRequest(BaseModel):
    path: str


def _to_response(f: KnowledgeFile) -> FileUploadResponse:
    return FileUploadResponse(
        file_id=f.file_id,
        kb_key=f.kb_key,
        filename=f.filename,
        mime=f.mime,
        size_bytes=f.size_bytes,
        seaweed_key=f.seaweed_key,
        status=str(f.status),
        uploaded_at=f.uploaded_at.isoformat(),
    )


def _resolve_allowlist() -> list[str]:
    raw = get_settings().KAG_FILE_PATH_ALLOWLIST
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


# Hardcoded deny list — system paths that should never be readable from
# path-mode uploads regardless of the allowlist setting.
_SYSTEM_PATH_PREFIXES: tuple[str, ...] = (
    "/etc/",
    "/proc/",
    "/sys/",
    "/dev/",
    "/root/",
    "/var/log/",
    "/var/lib/",
    "/boot/",
)


def _is_system_path(abs_path: str) -> bool:
    if abs_path in {p.rstrip("/") for p in _SYSTEM_PATH_PREFIXES}:
        return True
    return any(abs_path.startswith(p) for p in _SYSTEM_PATH_PREFIXES)


def _validate_path(path: str) -> str:
    abs_path = os.path.abspath(path)
    if _is_system_path(abs_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Path {path!r} is a system path; not allowed",
        )
    allowlist = _resolve_allowlist()
    if allowlist and not any(abs_path.startswith(os.path.abspath(p)) for p in allowlist):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Path {path!r} is not under KAG_FILE_PATH_ALLOWLIST",
        )
    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path {path!r} does not exist or is not a file",
        )
    return abs_path


@router.post(
    "/{kb_key}/files",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    kb_key: Annotated[str, PathParam(..., min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
    request: Request,
) -> FileUploadResponse:
    """Upload via multipart ``file`` field OR JSON body ``{"path": "..."}``.

    The request is parsed by Content-Type: multipart → ``file`` field;
    application/json → ``{"path": "..."}`` body. Manual parsing (vs
    letting FastAPI pick from two optional params) avoids the case
    where FastAPI's dependency auto-detection doesn't bind the JSON
    body to the Pydantic model parameter.
    """
    if caller.kb_key != kb_key or get_kb_store().get(kb_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    content_type = (request.headers.get("content-type") or "").lower()
    data: bytes
    filename: str
    mime: str
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, UploadFile) or not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="multipart request missing 'file' field",
            )
        data = await file.read()
        filename = file.filename
        mime = file.content_type or "application/octet-stream"
    elif content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON body: {exc}",
            ) from exc
        path = payload.get("path") if isinstance(payload, dict) else None
        if not path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON body must contain 'path'",
            )
        abs_path = _validate_path(path)
        filename = Path(abs_path).name
        with open(abs_path, "rb") as fp:
            data = fp.read()
        mime = "application/octet-stream"
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Use Content-Type multipart/form-data (with 'file' field) "
                "or application/json (with {'path': '...'})"
            ),
        )

    seaweed = SeaweedStore()
    record = KnowledgeFile(
        kb_key=kb_key,
        filename=filename,
        mime=mime,
        size_bytes=len(data),
        seaweed_key="",
    )
    record.seaweed_key = seaweed.key_for(kb_key, record.file_id, filename)
    seaweed.upload_file(record.seaweed_key, data, content_type=mime)

    get_file_store().add(record)

    # Also persist to ArangoDB kag_files so the vectorize / graph Celery
    # tasks (which read from the durable store, not the in-memory one)
    # can find the file. The in-memory FileStore stays as a fast list cache.
    try:
        from kag.db.arango import ArangoStore

        ArangoStore().database.collection("kag_files").insert(
            {
                "_key": record.file_id,
                "kb_key": record.kb_key,
                "filename": record.filename,
                "mime": record.mime,
                "size_bytes": record.size_bytes,
                "status": str(record.status),
                "error_msg": None,
                "seaweed_key": record.seaweed_key,
                "uploaded_at": record.uploaded_at.isoformat(),
                "processed_at": None,
            }
        )
    except Exception:
        log.exception("file.persist_arango_failed", file_id=record.file_id)
    log.info(
        "file.uploaded",
        kb_key=kb_key,
        file_id=record.file_id,
        filename=filename,
        size_bytes=len(data),
    )

    return _to_response(record)


@router.get("/{kb_key}/files", response_model=FileListResponse)
async def list_files(
    kb_key: Annotated[str, PathParam(..., min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> FileListResponse:
    if caller.kb_key != kb_key or get_kb_store().get(kb_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    files = get_file_store().list_for_kb(kb_key)
    return FileListResponse(files=[_to_response(f) for f in files])
