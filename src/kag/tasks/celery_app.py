"""Process-wide Celery application.

One Celery instance is shared by the API process (which only
``send_task``s / queries results) and the worker process (started
by ``kag worker``). The worker is the only consumer; the API
process never executes tasks in-process.

Broker + result backend: Redis (URL from ``Settings.REDIS_URL``).
Keys are namespaced with the ``kag:celery:`` prefix so we don't
clash with aibox-th's worker on the same Redis instance.
"""

from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]  # celery lacks PEP 561 stubs
from kombu import Queue  # type: ignore[import-untyped]  # kombu lacks PEP 561 stubs

from kag.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "kag",
    broker=_settings.REDIS_URL,
    backend=_settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_extended=True,
    task_time_limit=_settings.CELERY_TASK_TIME_LIMIT,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

# Namespacing: keep all keys, queues, tasks under a `kag:` prefix.
# Queues are explicit so aibox-th workers (running on the same
# Redis) cannot accidentally consume kag tasks.
celery_app.conf.task_default_queue = "kag.default"
celery_app.conf.task_queues = (
    Queue("kag.default", routing_key="kag.default"),
    Queue("kag.vectorize", routing_key="kag.vectorize"),
    Queue("kag.graph", routing_key="kag.graph"),
)
celery_app.conf.task_routes = {
    "kag.tasks.vectorize.vectorize_task": {"queue": "kag.vectorize"},
    "kag.tasks.graph.graph_task": {"queue": "kag.graph"},
}
