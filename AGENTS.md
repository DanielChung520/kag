# AGENTS.md

> Repo-specific guidance for OpenCode sessions working on **kag**.
> Read this before assuming anything from the README — kag is in foundation phase.

## Repo state (important)

- **Foundation phase refactor.** Most of `kag <subcommand>` commands advertised in README
  (`dev`, `start`, `stop`, `restart`, `status`, `logs`, `migrate`, `db-check`, `worker`) do
  **not exist yet**. `src/kag/cli.py` is a 30-line typer stub exposing only `kag hello`.
- `src/kag/` has no `main.py`, no `api/`, no `db/`, no `llm/`, no `worker/`, no `ingestion/`,
  no `graph/`, no `search/` — these are planned per `docs/ARCHITECTURE.md` but not implemented.
- `tests/` exists but `conftest.py` and `__init__.py` are empty (no tests yet).
- No CI config (`.github/` is absent). No pre-commit hooks.
- Implementation roadmap lives at `../.omo/plans/kag-standalone.md` (55 tasks, 9 waves) — not
  in this repo; check it before starting new work.

## Toolchain

- **Python 3.11** (`.python-version` pins it). Use `uv` only — never `pip install`.
  - `uv sync` to install
  - `uv run <cmd>` to execute anything (`uv run kag …`, `uv run pytest`, `uv run ruff`, …)
- **Lint:** `uv run ruff check .` (config: `line-length=100`, ignores E501)
- **Format:** `uv run ruff format .`
- **Typecheck:** `uv run mypy src` (strict mode; `disallow_untyped_defs=true`)
- **Test:** `uv run pytest`
  - `asyncio_mode = "auto"` — **don't** add `@pytest.mark.asyncio` to async tests
  - Markers: `integration` (needs ArangoDB/Qdrant/SeaweedFS/Redis/llm) and `slow`
  - Skip integration tests: `uv run pytest -m 'not integration'`
- **CLI entry:** `kag` → `kag.cli:main` (typer). Defined in `[project.scripts]` of `pyproject.toml`.

## Required env (`.env`)

`uv run kag …` will fail without `.env`. Copy `.env.example` and fill in at minimum:

- `KAG_API_KEY_PEPPER` (32+ random chars — generate with `openssl rand -hex 32`)
- `KAG_ADMIN_TOKEN` (32+ random chars)
- `ARANGO_URL`, `ARANGO_PASSWORD`
- `QDRANT_URL`, `QDRANT_VECTOR_DIM` (must match `EMBEDDING_MODEL` output dim; bge-m3 = 1024)
- `SEAWEED_URL`, `SEAWEED_ACCESS_KEY`, `SEAWEED_SECRET_KEY`, `SEAWEED_BUCKET`
- `REDIS_URL`
- `LLM_BASE_URL` (must end in `/v1`), `LLM_API_KEY`
- `EMBEDDING_MODEL`, `GRAPH_MODEL`, `VLM_MODEL`

`.env` is git-ignored; never commit it.

## Bootstrap order (local dev)

1. `uv sync`
2. `cp .env.example .env` and fill secrets
3. `uv run kag migrate` — idempotently creates 6 ArangoDB collections (kag_ prefix), verifies SeaweedFS bucket
4. `uv run kag dev` — uvicorn on `:8800` with reload
5. (separate terminal) `uv run kag worker` — Celery worker; required for file ingestion / graph extraction

## LLM provider — non-obvious constraints

- **dllm is the required LLM server** (team's unified layer, OpenAI-compatible at `:11400/v1`).
  vLLM and llama.cpp also work. **Ollama is NOT supported** — it returns non-OpenAI shapes for
  embeddings / json_mode / vision that have caused bugs. Don't add Ollama compatibility code.
- `LLM_BASE_URL` **must** include the `/v1` suffix; the OpenAI Python SDK is used directly.
- Switching providers is a `.env` change only; no code outside `kag.llm.*` should know which
  server is in use.
- `bge-m3` → 1024-dim embeddings (matches `QDRANT_VECTOR_DIM=1024`).
- VLM captioning uses OpenAI `image_url` content blocks; `qwen2.5-vl-8b` is the default.
- Models must be pulled on the dllm host (`dllm pull <model>`) before first use.

## Namespacing — share infra with `aibox-th`, do not collide

kag reuses the same ArangoDB / Qdrant / SeaweedFS / Redis as `aibox-th`. Every resource
kag owns must be namespaced:

| Datastore | kag namespace |
|---|---|
| ArangoDB | same database as aibox-th (e.g. `aistock`); all collections prefixed `kag_` |
| Qdrant | per-KB collections prefixed `kag_kb_<kb_key>` |
| SeaweedFS | bucket `kag` (separate from aibox-th's bucket); keys under `kag/` |
| Redis | different DB number (e.g. `/1`) OR rely on Celery key prefixing |

**Hard rule:** never read/write/drop any non-`kag_`-prefixed ArangoDB collection. Verify with
`uv run kag db-check` (once implemented).

## Auth model — two schemes, one service

| Endpoint class | Header | Token source |
|---|---|---|
| Admin (KB create/delete, ontology CRUD) | `Authorization: Bearer <token>` | `KAG_ADMIN_TOKEN` |
| Per-KB (file upload, search, pipeline trigger) | `X-KAG-API-Key: kag_xxx` | generated at KB creation, shown once |
| `GET /health` | none | always public |

- API keys are stored as `sha256(key + KAG_API_KEY_PEPPER)`; the pepper is process-level.
- Each KB has up to 5 active API keys; creating a new one auto-revokes the oldest.
- **A leaked admin token compromises the whole system; a leaked KB key compromises only that KB.**

## Architecture (when code lands)

3-layer, stateless, no in-memory cache, no sessions:

1. **HTTP API** (`kag.api.*`, FastAPI) — validate, auth, dispatch Celery, read directly for queries
2. **Celery workers** (`kag.worker.*`) — write-path only: parse, chunk, embed, extract graph
3. **Adapters** — `kag.db.{arango,qdrant,seaweedfs}` for datastores, `kag.llm.*` for LLM
4. **Business logic** lives in `kag.ingestion`, `kag.graph`, `kag.search` — importable from both API and worker (no logic duplication)

Production = 3 processes: `uvicorn`, `celery worker`, `cloudflared` (outbound tunnel to
`kag.aiconn.ai`, no inbound port). See `docs/DEPLOYMENT.md` for systemd units.

## Things this repo intentionally does NOT do (v0.x)

- No frontend, no UI of any kind
- No user accounts, sessions, OAuth/SSO
- No webhooks / event subscriptions
- No streaming responses
- No cross-KB search
- No vector store other than Qdrant, no graph store other than ArangoDB
- No LLM provider without fully OpenAI-compatible API (Azure deferred to v0.2)

Don't add scaffolding for any of these without an explicit ask.

## Key references

- `docs/ARCHITECTURE.md` — design, request lifecycles, failure modes, perf targets
- `docs/API.md` — HTTP contract, all endpoints, request/response shapes
- `docs/DATA_MODEL.md` — ArangoDB collections, Qdrant layout, schemas
- `docs/DEPLOYMENT.md` — prod deploy to `kag.aiconn.ai` (systemd units, cloudflared, scaling)
- `.env.example` — every env var with comments explaining the constraint
