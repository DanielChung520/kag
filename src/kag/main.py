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


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Exposed as a factory so tests can spin up isolated app instances.
    """
    app = FastAPI(
        title="kag",
        version=__version__,
        description="Knowledge-Augmented Generation service.",
    )
    app.include_router(api_router)
    return app


# Module-level instance required by ASGI servers (e.g. `uvicorn kag.main:app`).
app = create_app()
