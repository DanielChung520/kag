"""HTTP API routes.

Task 2: skeleton with `/health` only. Endpoint-level routers
(KB, files, ontologies, hybrid, jobs) land in later waves.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from kag import __version__

router = APIRouter()


class HealthDeps(BaseModel):
    """Placeholder for dependency health (filled in by Wave 2)."""


class HealthResponse(BaseModel):
    """Health check response body.

    Contract (see `docs/API.md`):
        - `status`: "ok" when this process is up; "degraded" if any dep is down.
        - `version`: service version.
        - `deps`: per-dependency status; empty until Wave 2 wires the checks.
    """

    status: str
    version: str
    deps: dict[str, object]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Service health check.

    Currently a stub: only reports process liveness. Real dependency
    checks (ArangoDB / Qdrant / SeaweedFS / Redis / LLM) are added in
    Wave 2 once the adapter clients exist.
    """
    return HealthResponse(status="ok", version=__version__, deps={})
