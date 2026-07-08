"""Ontology CRUD endpoints (admin auth).

| Method | Path | Auth |
|---|---|---|
| POST   | /api/v1/ontologies | Admin |
| GET    | /api/v1/ontologies | Admin |
| GET    | /api/v1/ontologies/{layer}/{name} | Admin |
| GET    | /api/v1/ontologies/{layer}/{name}/versions | Admin |
| GET    | /api/v1/ontologies/{layer}/{name}/versions/{v} | Admin |
| PUT    | /api/v1/ontologies/{layer}/{name} | Admin |
| DELETE | /api/v1/ontologies/{layer}/{name} | Admin |

PUT writes a new version of the ontology (immutable history; old
versions remain queryable). DELETE is a soft delete (status →
``deprecated``); data is preserved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    status,
)
from pydantic import BaseModel, Field

from kag.auth.dependencies import require_admin
from kag.models import LifecycleStatus, Ontology, OntologyLayer
from kag.ontology.store import get_ontology_store

log = structlog.get_logger("kag.api.ontology")
router = APIRouter(prefix="/api/v1/ontologies", tags=["ontologies"])

LayerPath = Annotated[
    OntologyLayer,
    Path(description="Ontology layer: basic | domain | major"),
]
NamePath = Annotated[
    str,
    Path(min_length=1, max_length=200),
]


# ── Request / response schemas ────────────────────────────────────────


class OntologyCreateRequest(BaseModel):
    layer: OntologyLayer
    name: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]


class OntologyDetail(BaseModel):
    layer: OntologyLayer
    name: str
    version: int
    parent_version: int | None
    status: LifecycleStatus
    created_at: datetime
    payload: dict[str, Any]


class OntologyListResponse(BaseModel):
    ontologies: list[OntologyDetail]


class OntologyVersionsResponse(BaseModel):
    layer: OntologyLayer
    name: str
    versions: list[OntologyDetail]


def _to_detail(o: Ontology) -> OntologyDetail:
    return OntologyDetail(
        layer=o.layer,
        name=o.name,
        version=o.version,
        parent_version=o.parent_version,
        status=o.status,
        created_at=o.created_at,
        payload=o.payload,
    )


def _payload_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid ontology payload: {exc}",
    )


# ── CRUD ──────────────────────────────────────────────────────────────


@router.post("", response_model=OntologyDetail, status_code=status.HTTP_201_CREATED)
async def create_ontology(
    body: OntologyCreateRequest,
    _: Annotated[None, Depends(require_admin)],
) -> OntologyDetail:
    store = get_ontology_store()
    try:
        ontology = store.put_new_version(
            name=body.name, layer=str(body.layer), payload=body.payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise _payload_error(exc) from exc
    log.info(
        "ontology.created",
        layer=str(body.layer),
        name=body.name,
        version=ontology.version,
    )
    return _to_detail(ontology)


@router.get("", response_model=OntologyListResponse)
async def list_ontologies(
    _: Annotated[None, Depends(require_admin)],
) -> OntologyListResponse:
    store = get_ontology_store()
    return OntologyListResponse(ontologies=[_to_detail(o) for o in store.list_latest()])


@router.get(
    "/{layer}/{name}",
    response_model=OntologyDetail,
)
async def get_ontology(
    layer: LayerPath,
    name: NamePath,
    _: Annotated[None, Depends(require_admin)],
) -> OntologyDetail:
    store = get_ontology_store()
    ontology = store.get_latest(str(layer), name)
    if ontology is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found")
    return _to_detail(ontology)


@router.get(
    "/{layer}/{name}/versions",
    response_model=OntologyVersionsResponse,
)
async def list_versions(
    layer: LayerPath,
    name: NamePath,
    _: Annotated[None, Depends(require_admin)],
) -> OntologyVersionsResponse:
    store = get_ontology_store()
    versions = store.list_versions(name)
    if not versions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found")
    return OntologyVersionsResponse(
        layer=layer, name=name, versions=[_to_detail(o) for o in versions]
    )


@router.get(
    "/{layer}/{name}/versions/{version}",
    response_model=OntologyDetail,
)
async def get_version(
    layer: LayerPath,
    name: NamePath,
    version: Annotated[int, Path(ge=1)],
    _: Annotated[None, Depends(require_admin)],
) -> OntologyDetail:
    store = get_ontology_store()
    ontology = store.get_version(str(layer), name, version)
    if ontology is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology version not found"
        )
    return _to_detail(ontology)


@router.put(
    "/{layer}/{name}",
    response_model=OntologyDetail,
)
async def put_ontology(
    body: OntologyCreateRequest,
    layer: LayerPath,
    name: NamePath,
    _: Annotated[None, Depends(require_admin)],
) -> OntologyDetail:
    store = get_ontology_store()
    try:
        ontology = store.put_new_version(name=name, layer=str(layer), payload=body.payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise _payload_error(exc) from exc
    log.info(
        "ontology.versioned",
        layer=str(layer),
        name=name,
        version=ontology.version,
    )
    return _to_detail(ontology)


@router.delete(
    "/{layer}/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_ontology(
    layer: LayerPath,
    name: NamePath,
    _: Annotated[None, Depends(require_admin)],
) -> None:
    store = get_ontology_store()
    if store.soft_delete(str(layer), name) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found")
    log.info("ontology.deleted", layer=str(layer), name=name)
