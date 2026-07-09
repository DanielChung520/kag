# kag — Knowledge-Augmented Generation Service

> **Standalone Python service for ontology management, document ingestion, vector indexing, and HybridRAG search with per-knowledge-base API key auth.**
>
> Born from extracting the knowledge-base + Ontology module of [aibox-th](https://github.com/) into an independent, deployable-anywhere microservice. **No frontend, no UI** — just a clean HTTP API and a service-management CLI.

---

## Why kag?

`aibox-th` is a desktop application with a deeply-integrated knowledge-base module. That coupling has costs:

- The KB module can't be reused by other services or agents
- It can only be deployed alongside the entire desktop stack
- It is tightly bound to the aibox-th auth system and Rust API gateway

`kag` is the **same capability, refactored into a clean standalone**:

- ✅ Deployable to any host as a pure Python service
- ✅ Exposes a stable HTTP API at `https://kag.aiconn.ai` for AI agents to consume
- ✅ Uses **per-KB API keys** for authorization (no shared user session required)
- ✅ Shares infrastructure with aibox-th (ArangoDB, Qdrant, SeaweedFS) — only the namespace differs
- ✅ **No frontend** — leaves UI to whoever consumes the API

---

## What kag Does

| Capability | Description |
|---|---|
| **Ontology Management** | CRUD on ontologies (Basic / Domain / Major three-layer model), JSON import, schema validation, immutable versioning |
| **Knowledge Base CRUD** | Create/list/get/delete knowledge bases; each KB gets a unique API key shown only once |
| **Document Ingestion** | Upload via multipart OR provide absolute file path; files stored in SeaweedFS, metadata in ArangoDB |
| **Parsing** | PDF, DOCX, Markdown, TXT — including **image extraction with VL-model captioning** for figures/diagrams |
| **Chunking + Vectorization** | Structure-aware chunking + dllm embeddings → per-KB Qdrant collections |
| **Graph Extraction** | LLM-driven NER/RE/RT guided by the KB's ontology; **cross-document entity dedup** |
| **HybridRAG Search** | Vector search + graph traversal + RRF fusion + boundary check (API key + ontology version + lifecycle) |
| **Evidence Search** | Returns ranked evidence units with conflict detection |
| **Service Management** | CLI for start/stop/status/logs (operate like a daemon) |

---

## Quick Start (Local Development)

### 1. Prerequisites

- **Python 3.11+** (managed automatically by `uv`)
- **uv** — install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **ArangoDB, Qdrant, SeaweedFS, Redis** — shared with `aibox-th` (see [Shared Infrastructure](#shared-infrastructure-with-aibox-th) below)
- **[dllm](https://github.com/dllm)** (preferred) — unified LLM serving layer with OpenAI-compatible API. Ollama / vLLM / llama.cpp also supported.

### 2. Clone & Install

```bash
cd ~/github/kag
uv sync
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set:
#   KAG_API_KEY_PEPPER=<32 random chars>
#   KAG_ADMIN_TOKEN=<32 random chars>
#   ARANGO_PASSWORD=<from aibox-th's .env>
#   SEAWEED_ACCESS_KEY, SEAWEED_SECRET_KEY
#   LLM_BASE_URL, LLM_API_KEY, EMBEDDING_MODEL, GRAPH_MODEL, VLM_MODEL
```

### 4. Bootstrap Schema

```bash
uv run kag migrate
```

This idempotently creates 6 ArangoDB collections (with indexes), Qdrant is collection-per-KB so no setup needed, and verifies the SeaweedFS bucket exists.

### 5. Verify connectivity (optional but recommended)

```bash
# Confirm each shared service responds before starting kag
curl -sf http://localhost:8529/_api/version | head -1    # ArangoDB
curl -sf http://localhost:6333/                          # Qdrant
curl -sf http://localhost:8888/                          # SeaweedFS
redis-cli -h localhost ping                              # Redis (PONG)
```

All four should respond. If any is missing, start the shared stack (see next section).

### 6. Run

```bash
uv run kag dev          # uvicorn --reload on :8800
# OR
uv run kag start        # daemon mode with PID file + logs

Visit `http://localhost:8800/docs` for interactive OpenAPI documentation.

### 7. First API Call

```bash
# Create a knowledge base (uses admin token, not API key)
curl -X POST http://localhost:8800/api/v1/knowledge-bases \
  -H "Authorization: Bearer $KAG_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "My First KB", "ontology_major": "sample"}'
# → {"kb_key": "...", "api_key": "kag_xxxxxxxxxx", ...}  ← save api_key!
```

---

## Shared Infrastructure (with `aibox-th`)

`kag` does **not** bundle its own ArangoDB / Qdrant / SeaweedFS / Redis instances. It connects to the same services that power the `aibox-th` desktop app, with strict namespacing so the two never collide.

### Namespacing rules

| Datastore | `kag` namespace | Hard rule |
|---|---|---|
| ArangoDB | same database as `aibox-th` (e.g. `aistock`); all collections prefixed `kag_` | **Never** read/write/drop any collection that does NOT start with `kag_`. |
| Qdrant | per-KB collections prefixed `kag_kb_<kb_key>` | Do not touch collections without the `kag_kb_` prefix. |
| SeaweedFS | bucket `kag` (separate from `aibox-th`'s bucket); keys under `kag/` | Use only the `kag` bucket; keys must start with `kag/`. |
| Redis | use a different DB number (e.g. `/1`) or rely on Celery key namespacing | Don't reuse `aibox-th`'s DB unless you've verified the keyspace. |

---

## External API

The full HTTP API is documented in **[`docs/API.md`](docs/API.md)**;
the canonical source is the live OpenAPI schema at
`http://localhost:8800/openapi.json` (also browsable at
`/docs` Swagger UI and `/redoc`).

Quick reference — the endpoints an external caller will use most:

- `GET /health` — liveness
- `POST /api/v1/knowledge-bases` — create a KB and receive its one-time API key (`kag_<32 base62>`)
- `GET /api/v1/knowledge-bases/{kb_key}/files` — list files
- `POST /api/v1/knowledge-bases/{kb_key}/files` — upload (multipart OR `{"path": "..."}` body)
- `POST /api/v1/knowledge-bases/{kb_key}/pipelines/all` — run vectorize + graph extraction
- `POST /api/v1/hybrid/search` — HybridRAG query (KB Key auth)

Two auth schemes (per-endpoint):

- `Authorization: Bearer <KAG_ADMIN_TOKEN>` — KB / ontology / job management
- `X-KAG-API-Key: kag_<32 base62>` — per-KB operations (file, search, pipeline)

**Keep docs in sync with the code**. The endpoint table in
`docs/API.md` is auto-checked against the live OpenAPI by
`scripts/check_api_sync.py`; that script runs in CI on every
PR. When you add or rename an endpoint — whether the change
comes from a kag-side PR or a downstream consumer of this
API — all three of these **must** land in the same commit:

1. Add the route under `src/kag/api/<topic>.py`.
2. Add the row to the **Endpoints Overview** table at the top
   of `docs/API.md` (format: `| METHOD | \`/path\` | Auth | Description |`).
3. Add a one-line entry to the README's "External API" section
   above if the endpoint is one a typical caller uses.
4. Run `uv run python scripts/check_api_sync.py` locally before
   pushing. The script exits non-zero on any drift, so a missed
   step fails the build.

Renames, path changes, and removals are breaking — bump
`/api/v1` → `/api/v2` and update the Versioning section.

This isolation is enforced in the adapter code (e.g. `QdrantStore.collection_name(kb_key)` returns `f"kag_kb_{kb_key}"`); there is no env knob to disable it.

### Default ports (when running on `localhost`)

| Service | Port | `kag` env var |
|---|---|---|
| ArangoDB | `8529` | `ARANGO_URL` |
| Qdrant | `6333` | `QDRANT_URL` |
| SeaweedFS | `8888` | `SEAWEED_URL` |
| Redis | `6379` | `REDIS_URL` |
| dllm (LLM) | `11400` | `LLM_BASE_URL=http://localhost:11400/v1` |

### One-time verification (before first `kag migrate`)

```bash
# ArangoDB: list databases
curl -s -u root:$ARANGO_PASSWORD http://localhost:8529/_api/database | jq '.result[].name'

# Qdrant: list existing collections (should be empty or only kag_/kag_kb_ prefixed)
curl -s http://localhost:6333/collections | jq '.result.collections[].name'

# SeaweedFS: verify kag bucket exists (create it manually if not)
curl -s http://localhost:8888/kag/  # 200 if bucket exists

# Redis: ensure the configured DB is reachable
redis-cli -h localhost -n 0 ping   # PONG
```

### Why no `docker-compose.dev.yml`?

The team's deployment model assumes `aibox-th` already provisions the four shared services (typically via `aibox-th/docker-compose.infra.yml`). `kag` simply points at them via `.env`. If you are setting up a fresh dev box without `aibox-th`, copy that compose file over and start it before working on `kag`.

---

## CLI Reference

The `kag` command is for local **service management**, not for data operations (use HTTP API for those).

| Command | Purpose |
|---|---|
| `kag dev` | Run dev server with auto-reload |
| `kag start` | Daemonize and start (writes PID + log files) |
| `kag stop` | Stop daemon (read PID, send SIGTERM) |
| `kag restart` | stop + start |
| `kag status` | Show service health (curl /health internally) |
| `kag logs -f` | Tail log file |
| `kag migrate` | Idempotently create/update schema |
| `kag db-check` | Verify all dependencies reachable + collections exist |
| `kag worker` | Start a Celery worker (separate process from HTTP server) |

---

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — System design, components, data flow
- **[docs/API.md](docs/API.md)** — HTTP API contract (all endpoints, request/response shapes, auth)
- **[docs/DATA_MODEL.md](docs/DATA_MODEL.md)** — ArangoDB collections, Qdrant collections, schemas
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — Production deployment to `kag.aiconn.ai`

---

## Project Status

🚧 **Early-stage refactor** — currently in foundation phase. See [`.omo/plans/kag-standalone.md`](../.omo/plans/kag-standalone.md) for the 55-task implementation plan organized into 9 parallel waves.

---

## License

MIT
