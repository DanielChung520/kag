# kag — Architecture

> Living document. Edit when design changes; review quarterly.

---

## System Overview

kag is a **stateless HTTP service** backed by a **Celery worker pool**, talking to four shared infrastructure components. The service has zero session state — every request carries credentials (admin token OR KB API key) and queries the data layer directly.

```
                       ┌──────────────────────────────────┐
                       │   AI Agents / External Services  │
                       │   (curl, OpenAI Function Calling, │
                       │    LangChain, custom SDKs)        │
                       └──────────────┬───────────────────┘
                                      │ HTTPS
                                      │ X-KAG-API-Key: kag_xxx
                                      ▼
                       ┌──────────────────────────────────┐
                       │   kag (FastAPI)  kag.aiconn.ai   │
                       │   ┌────────────┐  ┌────────────┐  │
                       │   │  HTTP API  │  │   CLI      │  │
                       │   │  /api/v1/* │  │  (typer)   │  │
                       │   └─────┬──────┘  └────────────┘  │
                       └─────────┼────────────────────────┘
                                 │ enqueue
                                 ▼
                       ┌──────────────────────────────────┐
                       │  Celery Workers  (kag worker)    │
                       │  • vectorize_task                │
                       │  • graph_extract_task            │
                       │  • vlm_caption_task              │
                       └────┬──────┬──────┬──────┬─────────┘
                            │      │      │      │
              ┌─────────────┘      │      │      └─────────────┐
              ▼                    ▼      ▼                    ▼
        ┌──────────┐         ┌──────────┐ ┌──────────┐     ┌──────────┐
        │ ArangoDB │         │  Qdrant  │ │SeaweedFS │     │   dllm   │
        │  (graph) │         │ (vector) │ │  (files) │     │ LLM/VLM  │
        └──────────┘         └──────────┘ └──────────┘     └──────────┘
        kag_* prefix          kag_kb_*     kag/{kb_key}/    bge-m3
                              collections                   qwen3-30b-a3b
                                                          qwen2.5-vl-8b
```

---

## Layered Architecture

### Layer 1 — HTTP API (FastAPI)

Responsibilities:
- Validate request shape (Pydantic models)
- Authenticate (`Authorization: Bearer <admin>` OR `X-KAG-API-Key: kag_xxx`)
- Authorize (per-KB API key → KB scope)
- Dispatch Celery tasks
- Read directly from ArangoDB / Qdrant for read-only endpoints

Non-responsibilities:
- ❌ No stateful in-memory caches (only Redis via Celery)
- ❌ No session management
- ❌ No business logic that's also done by the worker (logic must be importable from both)

### Layer 2 — Celery Workers

Responsibilities:
- All **write-path** work: file parsing, chunking, embedding, graph extraction
- Async, retryable, observable
- Idempotent: re-running on the same `file_id` produces the same end state

The HTTP API and the worker share the same Python package (`kag.*`), so any logic in `kag.ingestion`, `kag.graph`, `kag.search` is callable from either side.

### Layer 3 — Datastore Adapters (`kag.db.*`)

Each adapter is a thin, typed wrapper around a client library:

| Module | Wraps | Responsibility |
|---|---|---|
| `kag.db.arango` | `python-arango` | Connection + namespace + AQL helpers + `ensure_collections()` |
| `kag.db.qdrant` | `qdrant-client` | Connection + `ensure_collection(name, dim)` + search/upsert/delete |
| `kag.db.seaweedfs` | `boto3` | S3-style put/get/delete + presigned URLs + bucket ensure |

**Namespacing rule**: every collection / bucket / key that kag owns MUST carry the `kag_` prefix or live under a `kag/` directory. This is enforced in the adapter code (e.g., `QdrantStore.collection_name(kb_key)` returns `f"kag_kb_{kb_key}"`).

### Layer 4 — LLM/VLM Adapters (`kag.llm.*`)

A single `LLMClient` (built on the official `openai` Python SDK) exposes:
- `chat(model, messages, *, json_mode=False, temperature=None) -> str`
- `embed(model, texts) -> list[list[float]]`
- `vl_caption(model, image_bytes, prompt) -> str` — wraps image as data URL in an OpenAI `image_url` content block
- `health() -> LLMHealth` — `GET /v1/models` with a short timeout

**Provider priority: [dllm](https://github.com/dllm) first**, with any OpenAI-compatible server as a drop-in fallback. The client is configured via two env vars only:

| Env | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11400/v1` | Must include `/v1`. dllm's default port is 11400. |
| `LLM_API_KEY` | (empty for dev) | Bearer token. Required by dllm. |

Compatible servers (any of these work with zero code change):
- **[dllm](https://github.com/dllm)** — preferred, the team's unified LLM serving layer (Mac/MLX, NVIDIA/GB-10). Default models: `qwen3-30b-a3b-4bit`, `qwen2.5-vl-8b`, `bge-m3`.
- **vLLM** — for high-throughput NVIDIA deployments.
- **llama.cpp** server — for CPU-only / edge boxes.
- **Any OpenAI-API-compatible endpoint** — Azure OpenAI, OpenAI proper, etc. (note: Azure requires extra config for endpoint + api-version; deferred to v0.2).

⚠ **Ollama is NOT a supported backend.** Although Ollama exposes `/v1/chat/completions` and `/v1/embeddings` at first glance, it has historically returned non-OpenAI-compatible output shapes (e.g. embedding array structure, json_mode behavior, vision message format) that have caused subtle integration bugs. The team is **deprecating Ollama entirely** in favor of dllm. New deployments must use dllm; existing Ollama users should migrate.

**Why OpenAI SDK?** All supported servers (dllm, vLLM, llama.cpp) fully conform to the OpenAI spec at `/v1/chat/completions` and `/v1/embeddings`. Using the official `openai` Python SDK means:
- One client implementation, no vendor lock-in
- Built-in retry, streaming, tool-use, json_mode
- Type-safe request/response models
- Same code path in dev (any local OpenAI-compat server) and prod (dllm)

**No code outside `kag.llm.*` knows which provider is in use.** Switching is purely a `LLM_BASE_URL` env change.

---

## Process Topology

```
Production deployment (kag.aiconn.ai)
─────────────────────────────────────
  Cloudflare Tunnel ──→  uvicorn :8800  (kag serve, bound to 127.0.0.1)
                                     │
                                     └──→ Redis (broker)
                                              │
                                              ▼
                                     celery worker (1+ replicas)
                                              │
                                              ▼
                                     ArangoDB / Qdrant / SeaweedFS / dllm
```

Three processes:
1. **uvicorn** — the HTTP server (1+ replicas, bound to 127.0.0.1)
2. **celery worker** — async task execution (1+ replicas; can be scaled independently)
3. **cloudflared** — outbound-only tunnel that exposes `kag.aiconn.ai` to the public internet. No inbound port needed. TLS termination, rate limiting, and DDoS protection are handled by Cloudflare's edge.

Local development collapses 1 and 2 into a single Python process via `kag dev` (uvicorn + autoreload + a foreground celery worker, or use `kag start` for production-like).

---

## Request Lifecycle Examples

### Example A: Create Knowledge Base (Admin)

```
1. Client → POST /api/v1/knowledge-bases {name, ontology_major}
   Header: Authorization: Bearer <admin_token>

2. FastAPI dependency: requires_admin(admin_token)
   → validates token matches KAG_ADMIN_TOKEN

3. Endpoint handler:
   a. generate kb_key (uuid4)
   b. generate api_key (kag_ + 32 base62)
   c. hash api_key with KAG_API_KEY_PEPPER
   d. insert into kag_knowledge_bases (kb_key, name, ontology_major, api_key_hash, created_at)
   e. return {kb_key, api_key, ...}  ← raw api_key shown ONCE

4. Client stores api_key securely (e.g., env var, secrets manager)
```

### Example B: HybridRAG Search (Per-KB API Key)

```
1. Agent → POST /api/v1/hybrid/search {query, top_k}
   Header: X-KAG-API-Key: kag_xxxxxxxx

2. FastAPI dependency: current_kb(api_key)
   a. hash incoming key
   b. lookup kag_api_keys collection by hash
   c. if not found or revoked → 401
   d. load KB from kag_knowledge_bases
   e. update last_used_at (async, non-blocking)
   f. return KnowledgeBase

3. Endpoint handler:
   a. classify query (structure / entity / semantic)
   b. vector_search(query, kb.qdrant_collection) → list[chunk_result]
   c. graph_search(query, kb.arango_collection) → list[node_result]
   d. rrf_fusion(vector_results, graph_results)
   e. boundary_check(fused, kb.api_key, kb.ontology_version)
   f. evidence_analyzer(fused)
   g. return {evidence: [...], scores: [...]}
```

### Example C: File Upload (Worker Pipeline)

```
1. Client → POST /api/v1/knowledge-bases/{kb_key}/files
   Header: X-KAG-API-Key: kag_xxx
   Body: multipart 'file' OR JSON {path}

2. FastAPI handler:
   a. validate auth → current_kb
   b. (path mode) validate path against KAG_FILE_PATH_ALLOWLIST
   c. read file bytes
   d. upload to SeaweedFS at kag/{kb_key}/{file_id}/original
   e. insert kag_files {file_id, kb_key, status=pending, ...}
   f. enqueue vectorize_task.delay(file_id, kb_key)
   g. enqueue graph_extract_task.delay(file_id, kb_key)  (chained)
   h. return {file_id, job_ids: [...]}

3. Worker process picks up vectorize_task:
   a. fetch file from SeaweedFS
   b. parse → list[Block] (PDF / DOCX / etc.)
   c. for each image block: VLM caption
   d. chunk → list[Chunk]
   e. embed chunks → list[vector]
   f. upsert to Qdrant collection kag_kb_{kb_key}
   g. write chunk metadata to kag_chunks
   h. update kag_files.status = 'vectorized'
   i. update kag_jobs.status = 'success'

4. Worker process picks up graph_extract_task:
   a. load chunks from kag_chunks
   b. load ontology for this KB (kag_ontology + version)
   c. LLM extraction with ontology-guided prompt
   d. entity dedup across files (entity_key = normalized name + type)
   e. write to kag_graph_nodes, kag_graph_edges
   f. update kag_files.status = 'graphed'
```

---

## Auth Model

| Endpoint type | Auth | Where defined |
|---|---|---|
| `GET /health` | None | Always public |
| Admin endpoints (`POST /api/v1/knowledge-bases`, `POST /api/v1/ontologies/...`) | `Authorization: Bearer <KAG_ADMIN_TOKEN>` | For operator / control plane |
| Per-KB endpoints (file upload, search, pipeline trigger) | `X-KAG-API-Key: kag_xxx` | For agents and consuming services |

**Why two auth schemes?** Admins manage resources (KBs, ontologies); agents consume data. A leaked admin token compromises the whole system; a leaked per-KB API key only compromises that one KB's data.

**Hashing**: API keys are stored as `sha256(key + KAG_API_KEY_PEPPER)`. The pepper is a process-level secret, so even if the DB is exfiltrated, attackers can't brute-force keys.

**Key rotation**: A KB can have multiple active API keys. Each KB has at most N=5 active keys; older keys are auto-revoked when a new one is created. (Future: scheduled rotation.)

---

## Failure Modes

| Failure | Behavior | Recovery |
|---|---|---|
| ArangoDB unreachable | `/health` reports degraded; read endpoints return 503 | Operator fixes DB; no data loss |
| Qdrant unreachable | Same as above; vectorize_task retries with backoff | Same |
| SeaweedFS unreachable | File upload fails fast (502); existing KBs can still search | Re-upload after recovery |
| LLM server (dllm) unreachable | Vectorize/graph tasks fail; jobs marked `failed` with error | Tasks auto-retry on next file upload |
| Celery worker dies mid-task | Task is retried on next worker startup (Celery `acks_late=True`) | None needed |
| Two workers race on same file | Idempotent: both write same data; second write is a no-op via upsert | None needed |
| KB API key compromised | Admin creates new key; old key is revoked | Per-KB rotation |

---

## Out of Scope (v0.x)

These are **intentionally NOT in v0.x** to keep the scope small:

- ❌ User accounts, sessions, OAuth/SSO
- ❌ Frontend UI of any kind
- ❌ Multi-tenant isolation beyond per-KB API keys
- ❌ Vector store other than Qdrant
- ❌ Graph store other than ArangoDB
- ❌ LLM providers without fully OpenAI-compatible API (dllm, vLLM, llama.cpp all work out-of-the-box; Azure OpenAI needs extra config, deferred to v0.2). **Ollama is unsupported** — non-OpenAI output shapes in places.
- ❌ Webhooks / event subscriptions
- ❌ Streaming responses
- ❌ File deduplication at content level (hash-based dedup)
- ❌ Auto-versioning on ontology update (manual bump required)
- ❌ Cross-KB search

These can be added later without breaking the existing API contract.

---

## Performance Targets (v0.x)

- `POST /knowledge-bases` — < 50ms
- `POST /files` (multipart, 10MB) — < 1s (excluding parsing)
- `POST /hybrid/search` — < 500ms p95 for top_k=10 with 100k vectors
- Vectorize 1MB PDF — < 30s end-to-end (LLM-bound)
- Graph extract 1MB PDF — < 60s end-to-end (LLM-bound)
- API can serve 100 RPS sustained on 1 CPU / 1GB RAM (read-only; writes are async)
