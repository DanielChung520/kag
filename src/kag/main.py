"""FastAPI app factory and ASGI entry point.

Production servers should run:

    uvicorn kag.main:app --host 127.0.0.1 --port 8800 --workers $KAG_WORKERS

Local development can use either the same command or `python -m kag`
(this module), which runs uvicorn with sensible defaults.
"""

from __future__ import annotations

from fastapi import FastAPI

from kag import __version__
from kag.api import router as api_router
from kag.config import get_settings


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Exposed as a factory so tests can spin up isolated app instances.

    Calls :func:`kag.config.get_settings` eagerly so that a missing or
    invalid required env var causes the process to exit *before* uvicorn
    binds the port — operators see a clear pydantic validation error
    rather than a 502 from a half-initialized service.
    """
    get_settings()

    app = FastAPI(
        title="kag",
        version=__version__,
        description="Knowledge-Augmented Generation service.",
    )
    app.include_router(api_router)
    return app


# Module-level instance required by ASGI servers (e.g. `uvicorn kag.main:app`).
app = create_app()
