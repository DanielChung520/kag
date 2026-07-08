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
- **ArangoDB, Qdrant, SeaweedFS, Redis** — shared with `aibox-th` (see [DEPLOYMENT.md](docs/DEPLOYMENT.md#shared-infrastructure))
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

### 5. Run

```bash
uv run kag dev          # uvicorn --reload on :8800
# OR
uv run kag start        # daemon mode with PID file + logs

Visit `http://localhost:8800/docs` for interactive OpenAPI documentation.

### 6. First API Call

```bash
# Create a knowledge base (uses admin token, not API key)
curl -X POST http://localhost:8800/api/v1/knowledge-bases \
  -H "Authorization: Bearer $KAG_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "My First KB", "ontology_major": "sample"}'
# → {"kb_key": "...", "api_key": "kag_xxxxxxxxxx", ...}  ← save api_key!
```

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
