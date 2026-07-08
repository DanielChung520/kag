"""Prometheus metrics endpoint.

Exposes a minimal ``/metrics`` counter family in the same format
Prometheus scrapers expect. Counters tracked:

- ``kag_http_requests_total{path,method,status}`` — request count
  bucketed by route + verb + status
- ``kag_http_request_duration_seconds{method,path}`` — histogram
  of request latency (in seconds)

The metrics middleware (in :mod:`kag.main`) writes to these. The
endpoint is mounted at ``/metrics`` and excluded from the trace-id
middleware so Prometheus scrapes don't pollute request logs.
"""

from __future__ import annotations

import time
from collections import Counter

import structlog
from fastapi import APIRouter, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

log = structlog.get_logger("kag.api.metrics")
router = APIRouter(tags=["metrics"])

# We hand-roll the Prometheus text format rather than pulling in
# `prometheus_client`. The data model is simple: counter + histogram
# keyed by labels, and the kag observability needs are minimal.

# In-memory counters — fine for a single-process kag. If kag scales
# to multiple replicas, swap these for the official `prometheus_client`
# library which supports multi-process aggregation.
_request_count: Counter = Counter()  # type: ignore[type-arg]
_request_latency_ms: dict[tuple[str, str], list[float]] = {}


def _route_label(request: Request) -> str:
    """Return the FastAPI route template (e.g. ``/api/v1/jobs/{id}``)
    rather than the concrete URL, to keep cardinality bounded."""
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return request.url.path


def _format_metrics() -> str:
    lines: list[str] = []

    # ── Request counts ────────────────────────────────────────────
    lines.append("# HELP kag_http_requests_total HTTP request count")
    lines.append("# TYPE kag_http_requests_total counter")
    for (path, method, status), count in sorted(_request_count.items()):
        lines.append(
            f'kag_http_requests_total{{path="{path}",method="{method}",status="{status}"}} {count}'
        )

    # ── Latency histogram (simple: count, sum) ───────────────────
    lines.append("# HELP kag_http_request_duration_ms Request latency in ms")
    lines.append("# TYPE kag_http_request_duration_ms summary")
    for (method, path), samples in sorted(_request_latency_ms.items()):
        if not samples:
            continue
        lines.append(
            f'kag_http_request_duration_ms_count{{method="{method}",path="{path}"}} {len(samples)}'
        )
        lines.append(
            f'kag_http_request_duration_ms_sum{{method="{method}",path="{path}"}} {sum(samples):.2f}'
        )

    return "\n".join(lines) + "\n"


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus text-format scrape endpoint."""
    return Response(
        content=_format_metrics(),
        media_type="text/plain; version=0.0.4",
    )


def reset_metrics_for_test() -> None:
    """Test hook: zero in-memory metrics between test cases."""
    _request_count.clear()
    _request_latency_ms.clear()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count + latency on every response."""

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        # Skip recording for the metrics endpoint itself.
        if request.url.path != "/metrics":
            key = (_route_label(request), request.method, response.status_code)
            _request_count[key] += 1
            lk = (request.method, _route_label(request))
            _request_latency_ms.setdefault(lk, []).append(elapsed_ms)
        return response
