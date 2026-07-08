"""Celery tasks for kag's write path.

Tasks are organized by concern:

- :mod:`kag.tasks.vectorize` — parse + chunk + embed + upsert
- :mod:`kag.tasks.graph` — LLM-driven entity / relation extraction

Both register against the shared :data:`kag.tasks.celery_app.celery_app`.
"""

from __future__ import annotations

from kag.tasks.celery_app import celery_app

__all__ = ["celery_app"]
