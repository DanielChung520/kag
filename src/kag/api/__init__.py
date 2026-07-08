"""HTTP API: re-exports the combined router for ``main.py`` to mount."""

from __future__ import annotations

from fastapi import APIRouter

from kag.api import files, health, kb, ontology, pipelines

router = APIRouter()
router.include_router(health.router)
router.include_router(kb.router)
router.include_router(files.router)
router.include_router(ontology.router)
router.include_router(pipelines.router)

__all__ = ["router"]
