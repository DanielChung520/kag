"""Entry point for ``python -m kag``.

Forwards to the typer app, whose default (no subcommand) is to launch
the dev server in the foreground. Production should invoke
``uvicorn kag.main:app`` directly so worker count and signal handling
can be tuned.
"""

from __future__ import annotations

from kag.cli import app

if __name__ == "__main__":
    app()
