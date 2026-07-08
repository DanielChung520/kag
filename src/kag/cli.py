"""kag — service management CLI.

Wave 1 task 6: replaces the `kag hello` stub with the full set of
service-management subcommands. `migrate`, `db_check`, and `worker`
are present-but-stubbed; they land in Wave 2 (task 12) and Wave 5
(tasks 23-24) respectively.

State files (PID + logs) live under ``$KAG_STATE_DIR`` (default
``~/.kag/``) so multiple checkouts on one machine do not collide.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated

import typer

from kag import __version__

app = typer.Typer(
    name="kag",
    help="kag — Knowledge-Augmented Generation service management.",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)

logs_app = typer.Typer(help="View kag logs.", invoke_without_command=True)
app.add_typer(logs_app, name="logs")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8800
HEALTHCHECK_TIMEOUT_S = 10.0
KAG_STATE_DIR = Path(os.environ.get("KAG_STATE_DIR", Path.home() / ".kag"))
KAG_PID_FILE = KAG_STATE_DIR / "kag.pid"
KAG_LOG_FILE = KAG_STATE_DIR / "kag.log"
WORKER_PID_FILE = KAG_STATE_DIR / "worker.pid"
WORKER_LOG_FILE = KAG_STATE_DIR / "worker.log"


def _ensure_state_dir() -> None:
    KAG_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except ValueError:
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_running(pid_file: Path) -> int | None:
    pid = _read_pid(pid_file)
    if pid is None:
        return None
    if not _is_alive(pid):
        pid_file.unlink(missing_ok=True)
        return None
    return pid


def _wait_for_health(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def _run_uvicorn(
    host: str,
    port: int,
    workers: int,
    reload: bool,
) -> None:
    import uvicorn

    uvicorn.run(
        "kag.main:app",
        host=host,
        port=port,
        workers=workers,
        reload=reload,
        log_level="info",
    )


@app.callback()
def _root_callback(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show kag version and exit.",
            callback=lambda v: _print_version_and_exit(v),
            is_eager=True,
        ),
    ] = False,
) -> None:
    """kag — service management."""
    if ctx.invoked_subcommand is None:
        _run_uvicorn(DEFAULT_HOST, DEFAULT_PORT, workers=1, reload=True)


def _print_version_and_exit(value: bool) -> None:
    if value:
        typer.echo(f"kag {__version__}")
        raise typer.Exit()


@app.command()
def start(
    host: Annotated[str, typer.Option(help="Bind host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Bind port.")] = DEFAULT_PORT,
    workers: Annotated[int, typer.Option(help="Uvicorn worker processes.")] = 1,
) -> None:
    """Start kag as a background daemon (writes PID + log files)."""
    _ensure_state_dir()

    if (pid := _is_running(KAG_PID_FILE)) is not None:
        typer.echo(f"kag is already running (pid={pid})", err=True)
        raise typer.Exit(1)

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "kag.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--log-level",
        "info",
    ]

    with open(KAG_LOG_FILE, "ab") as log_fp:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    KAG_PID_FILE.write_text(str(proc.pid))
    typer.echo(f"kag started (pid={proc.pid}, log={KAG_LOG_FILE})")

    url = f"http://{host}:{port}/health"
    if _wait_for_health(url, timeout=HEALTHCHECK_TIMEOUT_S):
        typer.echo(f"  health: OK ({url})")
    else:
        typer.echo(
            f"  WARNING: {url} did not respond within "
            f"{HEALTHCHECK_TIMEOUT_S:.0f}s; check {KAG_LOG_FILE}",
            err=True,
        )


@app.command()
def stop(
    timeout: Annotated[float, typer.Option(help="Seconds to wait before SIGKILL.")] = 10.0,
) -> None:
    """Stop the background kag daemon."""
    pid = _is_running(KAG_PID_FILE)
    if pid is None:
        typer.echo("kag is not running", err=True)
        raise typer.Exit(1)

    typer.echo(f"stopping kag (pid={pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        typer.echo(f"failed to send SIGTERM: {exc}", err=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_running(KAG_PID_FILE) is None:
            break
        time.sleep(0.2)
    else:
        typer.echo(f"  process did not exit after {timeout:.0f}s; sending SIGKILL", err=True)
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)

    KAG_PID_FILE.unlink(missing_ok=True)
    typer.echo("kag stopped")


@app.command()
def restart(
    host: Annotated[str, typer.Option(help="Bind host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Bind port.")] = DEFAULT_PORT,
    workers: Annotated[int, typer.Option(help="Uvicorn worker processes.")] = 1,
) -> None:
    """stop + start."""
    if _is_running(KAG_PID_FILE) is not None:
        stop(timeout=10.0)
    start(host=host, port=port, workers=workers)


@app.command()
def status() -> None:
    """Show kag daemon status (PID + health check)."""
    pid = _is_running(KAG_PID_FILE)
    if pid is None:
        typer.echo("kag is NOT running")
        raise typer.Exit(1)

    typer.echo(f"kag is running (pid={pid})")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8800/health", timeout=2.0) as resp:
            typer.echo(f"health: {resp.status} {resp.read().decode()}")
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        typer.echo(f"health: UNREACHABLE ({exc})", err=True)
        raise typer.Exit(1) from exc


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Bind port.")] = DEFAULT_PORT,
    workers: Annotated[int, typer.Option(help="Uvicorn worker processes.")] = 1,
) -> None:
    """Run uvicorn in production mode (foreground, no reload)."""
    _run_uvicorn(host, port, workers=workers, reload=False)


@app.command()
def dev(
    host: Annotated[str, typer.Option(help="Bind host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Bind port.")] = DEFAULT_PORT,
) -> None:
    """Run uvicorn dev server (foreground, with auto-reload)."""
    _run_uvicorn(host, port, workers=1, reload=True)


@app.command()
def migrate() -> None:
    """Idempotently create/update database schema.

    Stub: implementation lands in Wave 2 task 12.
    """
    typer.echo(
        "kag migrate: not yet implemented (lands in Wave 2 task 12).",
        err=True,
    )
    raise typer.Exit(1)


@app.command(name="db-check")
def db_check() -> None:
    """Verify all dependencies reachable + collections exist.

    Stub: implementation lands in Wave 2.
    """
    typer.echo(
        "kag db-check: not yet implemented (lands in Wave 2).",
        err=True,
    )
    raise typer.Exit(1)


@app.command()
def worker() -> None:
    """Start a Celery worker (separate process from the HTTP server).

    Stub: implementation lands in Wave 5 tasks 23-24.
    """
    typer.echo(
        "kag worker: not yet implemented (lands in Wave 5 tasks 23-24).",
        err=True,
    )
    raise typer.Exit(1)


@logs_app.callback()
def logs_main(
    ctx: typer.Context,
    follow: Annotated[
        bool, typer.Option("-f", "--follow", help="Follow the log (like tail -f).")
    ] = False,
    lines: Annotated[int, typer.Option("-n", help="Number of lines to show.")] = 100,
) -> None:
    """Show the last N lines of the kag log (use -f to follow)."""
    if ctx.invoked_subcommand is not None:
        return

    if not KAG_LOG_FILE.exists():
        typer.echo(
            f"no log file at {KAG_LOG_FILE}; has kag been started?",
            err=True,
        )
        raise typer.Exit(1)

    cmd = ["tail", "-n", str(lines)]
    if follow:
        cmd.append("-f")
    cmd.append(str(KAG_LOG_FILE))
    os.execvp("tail", cmd)


if __name__ == "__main__":
    app()


def main() -> None:
    """Console-script entry point. Invokes the typer app."""
    app()
