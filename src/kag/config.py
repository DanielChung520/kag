"""Application configuration.

Reads environment variables (and optionally `.env`) into a typed
:class:`Settings` instance. Use :func:`get_settings` for cached access —
settings are intended to be immutable after process start.

Required env vars (no defaults) — the process will refuse to start if any
of these are missing:

    KAG_API_KEY_PEPPER   >= 32 random chars; used for per-KB API key hashing.
    KAG_ADMIN_TOKEN      >= 32 random chars; admin endpoints auth.
    ARANGO_URL           e.g. http://localhost:8529
    ARANGO_DB            e.g. aistock
    ARANGO_USER          e.g. root
    ARANGO_PASSWORD
    QDRANT_URL           e.g. http://localhost:6333
    SEAWEED_URL          e.g. http://localhost:8888
    SEAWEED_BUCKET       e.g. kag
    SEAWEED_ACCESS_KEY
    SEAWEED_SECRET_KEY
    REDIS_URL            e.g. redis://localhost:6379/0
    LLM_BASE_URL         MUST end with '/v1' (e.g. http://localhost:11400/v1)
    EMBEDDING_MODEL      e.g. bge-m3
    GRAPH_MODEL          e.g. qwen3-30b-a3b-4bit
    VLM_MODEL            e.g. qwen2.5-vl-8b

See `.env.example` for the full list with comments.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
EnvName = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """All kag configuration in one flat namespace.

    Flat (no nested groups) so env var names == field names, which keeps
    pydantic-settings error messages straightforward for operators.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── Core ────────────────────────────────────────────────────────────
    KAG_ENV: EnvName = "development"
    KAG_LOG_LEVEL: LogLevel = "INFO"
    KAG_API_KEY_PEPPER: str = Field(min_length=32)
    KAG_ADMIN_TOKEN: str = Field(min_length=32)

    # ─── HTTP server ─────────────────────────────────────────────────────
    KAG_HOST: str = "127.0.0.1"
    KAG_PORT: int = 8800
    KAG_WORKERS: int = 1

    # ─── ArangoDB (shared with aibox-th) ─────────────────────────────────
    ARANGO_URL: str
    ARANGO_DB: str
    ARANGO_USER: str
    ARANGO_PASSWORD: str

    # ─── Qdrant (shared; per-KB collections prefixed kag_kb_) ───────────
    QDRANT_URL: str
    QDRANT_API_KEY: str = ""
    QDRANT_VECTOR_DIM: int = 1024

    # ─── SeaweedFS (shared; bucket "kag", keys under kag/) ──────────────
    SEAWEED_URL: str
    SEAWEED_BUCKET: str
    SEAWEED_ACCESS_KEY: str
    SEAWEED_SECRET_KEY: str

    # ─── Redis (Celery broker + result backend) ─────────────────────────
    REDIS_URL: str
    CELERY_TASK_TIME_LIMIT: int = 600

    # ─── LLM (dllm-first; any OpenAI-compatible server) ─────────────────
    LLM_BASE_URL: str
    LLM_API_KEY: str = ""
    EMBEDDING_MODEL: str
    GRAPH_MODEL: str
    VLM_MODEL: str

    # LLM tunables
    LLM_TIMEOUT: float = 300.0
    LLM_JSON_RETRY: int = 1
    LLM_MAX_RETRIES: int = 2
    LLM_TEMPERATURE_GRAPH: float = 0.1
    LLM_TEMPERATURE_VL: float = 0.3
    LLM_MAX_TOKENS_GRAPH: int = 4096
    LLM_MAX_TOKENS_VL: int = 1024

    # ─── Misc tunables ───────────────────────────────────────────────────
    KAG_FILE_PATH_ALLOWLIST: str = ""
    KAG_DOWNLOAD_URL_TTL: int = 3600
    KAG_VECTOR_CHUNK_SIZE: int = 512
    KAG_VECTOR_CHUNK_OVERLAP: int = 64
    KAG_GRAPH_MAX_ENTITIES_PER_FILE: int = 500

    # ─── Observability ───────────────────────────────────────────────────
    KAG_TRACING_ENABLED: bool = False
    KAG_OTLP_ENDPOINT: str = ""

    # ─── Validators ──────────────────────────────────────────────────────
    @field_validator("LLM_BASE_URL")
    @classmethod
    def _llm_base_url_must_end_with_v1(cls, v: str) -> str:
        """All supported backends (dllm, vLLM, llama.cpp) expose OpenAI
        at `/v1/...`. Reject anything else so a misconfigured env fails
        loudly instead of producing 404s at runtime.
        """
        if not v.rstrip("/").endswith("/v1"):
            raise ValueError(
                f"LLM_BASE_URL must end with '/v1' (got {v!r}). Example: http://localhost:11400/v1"
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance.

    Cached so the .env file is parsed at most once. Treat the returned
    object as immutable; mutating it would affect every caller.
    """
    return Settings()  # type: ignore[call-arg]
