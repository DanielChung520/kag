# syntax=docker/dockerfile:1.7

# ── Stage 1: build (uv) ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

# Install uv once; pinned via the project's pyproject.toml / uv.lock.
RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /build

# Copy only the manifest first for better layer caching.
COPY pyproject.toml uv.lock ./

# Install deps into a relocatable venv. Exclude the project itself
# so the runtime image can COPY the app code separately.
RUN uv sync --frozen --no-install-project --no-dev

# Now copy the source and install the project itself (editable is
# fine; we just need the import paths resolvable).
COPY src ./src
RUN uv sync --frozen --no-dev

# ── Stage 2: runtime (slim) ──────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user; matches the systemd unit's `User=kag`.
RUN groupadd --system kag && useradd --system --gid kag --home /app kag

WORKDIR /app

# Copy the prebuilt venv + source from the builder.
COPY --from=builder --chown=kag:kag /build/.venv ./.venv
COPY --from=builder --chown=kag:kag /build/src ./src

# The CLI is registered as `kag = kag.cli:main` in pyproject.toml,
# so it lives in the venv bin. The runtime CMD assumes the operator
# has mounted /app/.env (or passes env vars via the orchestrator).
USER kag

EXPOSE 8800

# Default command: bind to localhost (cloudflared-friendly). Use
# `kag start` for daemon mode or `kag worker` for the worker role
# — pick via the orchestrator (docker-compose / systemd unit).
CMD ["kag", "serve", "--host", "127.0.0.1", "--port", "8800"]
