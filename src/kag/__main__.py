"""Entry point for `python -m kag`.

Runs uvicorn in the foreground. For production (no reload, signal handling,
multi-worker) prefer invoking uvicorn directly so worker count can be tuned.
"""

from __future__ import annotations

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8800


def main() -> None:
    """Start the uvicorn dev server."""
    uvicorn.run(
        "kag.main:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
