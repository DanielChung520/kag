"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from kag import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response body (see ``docs/API.md``)."""

    status: str
    version: str
    deps: dict[str, object]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe. Dependency checks land in a follow-up."""
    return HealthResponse(status="ok", version=__version__, deps={})
