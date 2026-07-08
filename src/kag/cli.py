"""kag CLI entrypoint.

Wave 1, task 6 will expand this into the full typer-based service
management CLI (start/stop/status/logs/etc.). For now this is a stub
so `uv run kag --help` works and the project structure is sound.
"""
from __future__ import annotations

import typer

app = typer.Typer()


@app.callback()
def callback() -> None:
    """kag — Knowledge-Augmented Generation service."""


@app.command()
def hello() -> None:
    """Smoke-test command."""
    typer.echo("Hello from kag! (placeholder CLI — see docs/ARCHITECTURE.md)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
