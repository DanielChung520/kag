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

import json
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Request,
    status,
)
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from kag.auth.dependencies import require_admin
from kag.models import LifecycleStatus, Ontology, OntologyLayer
from kag.ontology.schema import validate_payload
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


# ── Task 19: import endpoint ──────────────────────────────────────────


class ImportResponse(BaseModel):
    accepted: bool
    dry_run: bool
    layer: OntologyLayer
    name: str
    version: int | None = None


@router.post(
    "/import",
    response_model=ImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_ontology(
    request: Request,
    _: Annotated[None, Depends(require_admin)],
) -> ImportResponse:
    """Import via multipart file upload OR JSON body.

    - ``multipart/form-data`` with ``file`` field → reads file bytes
    - ``application/json`` with body ``{"payload": {...}, "dry_run": bool}``
    - ``dry_run=true`` validates without persisting
    """
    content_type = (request.headers.get("content-type") or "").lower()
    payload: dict[str, Any]
    dry_run: bool = False
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="multipart request missing 'file' field",
            )
        raw = await upload.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON in uploaded file: {exc}",
            ) from exc
        dry_run_raw = form.get("dry_run")
        if dry_run_raw is not None:
            dry_run = str(dry_run_raw).lower() in {"1", "true", "yes"}
    elif content_type.startswith("application/json"):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON body: {exc}",
            ) from exc
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON body must be an object",
            )
        if "payload" not in body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON body must contain 'payload'",
            )
        payload = body["payload"]
        dry_run = bool(body.get("dry_run", False))
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Use Content-Type multipart/form-data (with 'file' field) "
                "or application/json (with 'payload' and optional 'dry_run')"
            ),
        )

    try:
        validated = validate_payload(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payload validation failed: {exc}",
        ) from exc

    if dry_run:
        log.info(
            "ontology.import.dryrun",
            layer=str(validated.layer),
            name=validated.name,
        )
        return ImportResponse(
            accepted=True,
            dry_run=True,
            layer=OntologyLayer(str(validated.layer)),
            name=validated.name,
            version=None,
        )

    store = get_ontology_store()
    ontology = store.put_new_version(
        name=validated.name, layer=str(validated.layer), payload=payload
    )
    log.info(
        "ontology.imported",
        layer=str(validated.layer),
        name=validated.name,
        version=ontology.version,
    )
    return ImportResponse(
        accepted=True,
        dry_run=False,
        layer=OntologyLayer(str(validated.layer)),
        name=validated.name,
        version=ontology.version,
    )


# ── Task 22: graph export ────────────────────────────────────────────


class GraphNode(BaseModel):
    id: str
    layer: OntologyLayer
    kind: str  # "entity_class"
    label: str | None = None
    description: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str


class GraphResponse(BaseModel):
    layer: OntologyLayer
    name: str
    version: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]


def _build_graph(ontology: Ontology) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Convert a Domain/Major ontology into a node+edge graph for G6.

    Basic ontologies have no entity classes / properties; return empty.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    payload: dict[str, Any] = ontology.payload
    classes: list[dict[str, Any]] = payload.get("entity_classes", []) or []
    props: list[dict[str, Any]] = payload.get("object_properties", []) or []
    for c in classes:
        nodes.append(
            GraphNode(
                id=c["name"],
                layer=ontology.layer,
                kind="entity_class",
                label=c.get("name"),
                description=c.get("description"),
            )
        )
    for p in props:
        edges.append(GraphEdge(source=p["domain"], target=p["range"], label=p["name"]))
    return nodes, edges


@router.get(
    "/{layer}/{name}/graph",
    response_model=GraphResponse,
)
async def get_graph(
    layer: LayerPath,
    name: NamePath,
    _: Annotated[None, Depends(require_admin)],
) -> GraphResponse:
    store = get_ontology_store()
    ontology = store.get_latest(str(layer), name)
    if ontology is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found")
    nodes, edges = _build_graph(ontology)
    return GraphResponse(
        layer=layer,
        name=name,
        version=ontology.version,
        nodes=nodes,
        edges=edges,
    )


# ── Task 21: change tracking (diff) ──────────────────────────────────


class OntologyDiff(BaseModel):
    layer: OntologyLayer
    name: str
    from_version: int
    to_version: int
    added_entity_classes: list[str]
    removed_entity_classes: list[str]
    added_object_properties: list[str]
    removed_object_properties: list[str]


@router.get(
    "/{layer}/{name}/versions/{version}/diff",
    response_model=OntologyDiff,
)
async def get_diff(
    layer: LayerPath,
    name: NamePath,
    version: Annotated[int, Path(ge=1)],
    against: Annotated[
        int | None,
        Path(description="Base version to diff against; defaults to version-1."),
    ] = None,
    _: Annotated[None, Depends(require_admin)] = None,
) -> OntologyDiff:
    """Diff a version against an earlier one (default: the previous)."""
    store = get_ontology_store()
    base = (
        store.get_version(str(layer), name, against)
        if against is not None
        else store.get_version(str(layer), name, max(1, version - 1))
    )
    target = store.get_version(str(layer), name, version)
    if target is None or base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ontology version not found",
        )

    def _names(payload: dict[str, Any], key: str) -> set[str]:
        items = payload.get(key, []) or []
        return {it["name"] for it in items}

    base_classes = _names(base.payload, "entity_classes")
    target_classes = _names(target.payload, "entity_classes")
    base_props = _names(base.payload, "object_properties")
    target_props = _names(target.payload, "object_properties")

    return OntologyDiff(
        layer=layer,
        name=name,
        from_version=base.version,
        to_version=target.version,
        added_entity_classes=sorted(target_classes - base_classes),
        removed_entity_classes=sorted(base_classes - target_classes),
        added_object_properties=sorted(target_props - base_props),
        removed_object_properties=sorted(base_props - target_props),
    )
