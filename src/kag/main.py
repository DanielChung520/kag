"""FastAPI app factory and ASGI entry point.

Production servers should run:

    uvicorn kag.main:app --host 127.0.0.1 --port 8800 --workers $KAG_WORKERS

Local development can use either the same command or `python -m kag`
(this module), which runs uvicorn with sensible defaults.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from starlette.responses import Response

from kag import __version__
from kag.api import router as api_router
from kag.config import get_settings
from kag.logging_config import REQUEST_ID_HEADER, configure_logging, new_trace_id

_request_log = structlog.get_logger("kag.request")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Exposed as a factory so tests can spin up isolated app instances.

    Calls :func:`kag.config.get_settings` eagerly so that a missing or
    invalid required env var causes the process to exit *before* uvicorn
    binds the port — operators see a clear pydantic validation error
    rather than a 502 from a half-initialized service.
    """
    settings = get_settings()
    configure_logging(settings.KAG_LOG_LEVEL)

    app = FastAPI(
        title="kag",
        version=__version__,
        description="Knowledge-Augmented Generation service.",
    )
    app.include_router(api_router)
    app.middleware("http")(_trace_id_middleware)
    return app


async def _trace_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Bind trace_id to the request's structlog context and echo it
    back in the response header so callers can correlate logs.
    """
    trace_id = request.headers.get(REQUEST_ID_HEADER) or new_trace_id()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        method=request.method,
        path=request.url.path,
    )

    _request_log.info("request.start")

    try:
        response: Response = await call_next(request)
    except Exception:
        _request_log.exception("request.error")
        raise

    response.headers[REQUEST_ID_HEADER] = trace_id
    _request_log.info("request.finish", status_code=response.status_code)
    return response


# Module-level instance required by ASGI servers (e.g. `uvicorn kag.main:app`).
app = create_app()
