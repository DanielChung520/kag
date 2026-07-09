# kag — HTTP API Reference

> Auto-generated OpenAPI at `/docs` (Swagger UI) and `/redoc`.
> This document is the human-readable summary; the OpenAPI spec is the source of truth for clients.

---

## Base URL

| Environment | URL |
|---|---|
| Local dev | `http://localhost:8800` |
| Production | `https://kag.aiconn.ai` |

All endpoints are prefixed with `/api/v1`. Versioning follows the URL path; breaking changes bump to `/api/v2`.

---

## Authentication

Two schemes, applied per-endpoint:

| Scheme | Header | Used for |
|---|---|---|
| Admin | `Authorization: Bearer <KAG_ADMIN_TOKEN>` | KB creation/deletion, Ontology management |
| KB API Key | `X-KAG-API-Key: kag_<32 base62 chars>` | File ops, search, pipeline trigger on a specific KB |

API keys are issued **once** at KB creation. The raw key is returned in the response body; subsequent reads expose only the key's metadata (label, created_at, last_used_at). To rotate, call `POST /knowledge-bases/{kb_key}/api-keys` (old key auto-revoked after grace period, default 24h).

---

## Endpoints Overview

> **The canonical list of routes is the running server's OpenAPI
> schema at `/openapi.json`. This table mirrors it; a CI check
> (`scripts/check_api_sync.py`) fails the build if they drift.**

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness + version |
| POST | `/api/v1/knowledge-bases` | Admin | Create KB (returns `api_key` once) |
| GET | `/api/v1/knowledge-bases` | Admin | List all KBs |
| GET | `/api/v1/knowledge-bases/{kb_key}` | Admin OR KB Key | Get KB detail |
| PATCH | `/api/v1/knowledge-bases/{kb_key}` | Admin | Update name/description |
| DELETE | `/api/v1/knowledge-bases/{kb_key}` | Admin | Soft delete (status=deprecated) |
| POST | `/api/v1/knowledge-bases/{kb_key}/files` | KB Key | Upload file (multipart OR path) |
| GET | `/api/v1/knowledge-bases/{kb_key}/files` | KB Key | List files |
| POST | `/api/v1/knowledge-bases/{kb_key}/pipelines/vectorize` | KB Key | Trigger vectorization for pending files |
| POST | `/api/v1/knowledge-bases/{kb_key}/pipelines/extract-graph` | KB Key | Trigger graph extraction for vectorized files |
| POST | `/api/v1/knowledge-bases/{kb_key}/pipelines/all` | KB Key | Run vectorize + extract-graph in sequence |
| GET | `/api/v1/jobs/{job_id}` | Admin OR KB Key (owning) | Job status (live from Celery + persisted) |
| POST | `/api/v1/jobs/{job_id}/abort` | Admin OR KB Key | Revoke the Celery task |
| POST | `/api/v1/jobs/{job_id}/retry` | Admin OR KB Key | Re-enqueue with a fresh Celery task id |
| GET | `/api/v1/jobs/{job_id}/logs` | Admin OR KB Key | Return last task result + tail + traceback |
| POST | `/api/v1/ontologies` | Admin | Create ontology (v1) |
| POST | `/api/v1/ontologies/import` | Admin | Import ontology JSON (file OR inline) |
| GET | `/api/v1/ontologies` | Admin | List latest version of every ontology |
| GET | `/api/v1/ontologies/{layer}/{name}` | Admin | Get the latest version |
| PUT | `/api/v1/ontologies/{layer}/{name}` | Admin | Update (bumps version, old version preserved) |
| DELETE | `/api/v1/ontologies/{layer}/{name}` | Admin | Soft delete (status=deprecated) |
| GET | `/api/v1/ontologies/{layer}/{name}/versions` | Admin | All versions for an ontology |
| GET | `/api/v1/ontologies/{layer}/{name}/versions/{version}` | Admin | Get a specific version |
| GET | `/api/v1/ontologies/{layer}/{name}/versions/{version}/diff` | Admin | Diff two versions (added/removed entities + relations) |
| GET | `/api/v1/ontologies/{layer}/{name}/graph` | Admin | Export nodes + edges (for G6 / similar viz) |
| POST | `/api/v1/hybrid/search` | KB Key | HybridRAG (vector + graph fusion + boundary check) |
| POST | `/api/v1/hybrid/evidence` | KB Key | Same as `/search`; always returns the full `evidence` list |

`{layer}` ∈ `basic` | `domain` | `major`.
`{kb_key}` is the kb's uuid hex (32 chars).
KB API keys are issued **once** at KB creation. Rotation is
not yet exposed as a separate endpoint (tracked for v0.1.1); for
now, delete the KB and re-create to rotate.

---

## Endpoint Details

### `GET /health`

**Auth**: None

**Response 200**:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "deps": {
    "arango": {"ok": true, "version": "3.12.0"},
    "qdrant": {"ok": true, "version": "1.16.0"},
    "seaweedfs": {"ok": true},
    "redis": {"ok": true},
    "llm": {
      "ok": true,
      "provider": "dllm",
      "base_url": "http://10.0.0.20:11400/v1",
      "models_available": ["qwen3-30b-a3b-4bit", "qwen2.5-vl-8b", "bge-m3"]
    }
  }
}
```

**Response 503** (any dep down):
```json
{
  "status": "degraded",
  "deps": {
    "arango": {"ok": false, "error": "connection refused"},
    "llm": {"ok": false, "error": "401 unauthorized", "base_url": "..."}
  }
}
```

---

### `POST /api/v1/knowledge-bases`

**Auth**: Admin

**Request**:
```json
{
  "name": "Production Manuals",
  "description": "...",
  "ontology_major": "manufacturing_v1",
  "ontology_version": 2
}
```

**Response 201**:
```json
{
  "kb_key": "9c1e3f0a-...-uuid",
  "name": "Production Manuals",
  "api_key": "kag_AbCdEf123...",   ← shown ONCE, store it now
  "ontology_major": "manufacturing_v1",
  "ontology_version": 2,
  "created_at": "2026-07-08T12:00:00Z"
}
```

**Errors**:
- `400` — invalid request body
- `401` — missing/invalid admin token
- `409` — KB name already exists (if name uniqueness is enforced; v0.1 may not enforce)

---

### `POST /api/v1/knowledge-bases/{kb_key}/files`

**Auth**: KB API Key

**Request (multipart)**:
```
POST /api/v1/knowledge-bases/9c1e.../files
Headers:
  X-KAG-API-Key: kag_xxx
  Content-Type: multipart/form-data
Body:
  file: <binary>
  options.metadata: {"source": "manual-v3", "language": "zh-TW"}  (optional JSON)
```

**Request (path reference)**:
```json
POST /api/v1/knowledge-bases/9c1e.../files
Headers:
  X-KAG-API-Key: kag_xxx
  Content-Type: application/json

{
  "path": "/data/docs/handbook.pdf",
  "metadata": {"source": "manual-v3"}
}
```

**Response 202** (accepted, processing async):
```json
{
  "file_id": "f7d8e9a0-...",
  "filename": "handbook.pdf",
  "size_bytes": 1048576,
  "status": "pending",
  "job_ids": ["j_abc123", "j_def456"],
  "uploaded_at": "2026-07-08T12:01:00Z"
}
```

**Errors**:
- `400` — file too large, unsupported MIME, invalid path
- `403` — path not in KAG_FILE_PATH_ALLOWLIST (path mode)
- `401` — missing/invalid API key
- `413` — payload too large
- `429` — rate limited

---

### `POST /api/v1/knowledge-bases/{kb_key}/pipelines/vectorize`

**Auth**: KB API Key

**Request**:
```json
{
  "file_ids": ["f7d8e9a0-..."],   // optional; empty = all pending files
  "force": false                  // if true, re-vectorize even already-done files
}
```

**Response 202**:
```json
{
  "job_ids": ["j_abc123"],
  "files_queued": 1
}
```

---

### `POST /api/v1/ontologies/import`

**Auth**: Admin

**Request (JSON inline)**:
```json
{
  "layer": "major",
  "name": "manufacturing_v1",
  "description": "...",
  "inherits_from": "manufacturing_basic",
  "version": 1,
  "payload": {
    "entity_classes": [...],
    "object_properties": [...],
    "use_cases": [...],
    "tags": [...]
  }
}
```

**Request (multipart file upload)**:
```
file: ontology.json
layer: major
name: manufacturing_v1
inherits_from: manufacturing_basic
```

**Response 201**:
```json
{
  "ontology_key": "manufacturing_v1",
  "version": 1,
  "layer": "major",
  "validation_warnings": []   // non-fatal issues
}
```

**Errors**:
- `400` — invalid JSON, schema validation failed (response includes list of issues)
- `409` — `(name, version)` already exists

---

### `POST /api/v1/hybrid/search`

**Auth**: KB API Key

**Request**:
```json
{
  "query": "產線 A 在 6 月的良率異常原因",
  "top_k": 10,
  "ontology_version": 2,        // optional; defaults to KB's pinned version
  "search_mode": "auto",        // "auto" | "vector" | "graph" | "hybrid"
  "include_evidence": true,
  "min_score": 0.0,
  "filters": {
    "file_ids": [],             // optional scope
    "date_range": {"from": "...", "to": "..."}
  }
}
```

**Response 200**:
```json
{
  "query": "...",
  "search_mode_used": "hybrid",
  "results": [
    {
      "chunk_id": "...",
      "text": "...",
      "score": 0.87,
      "source": {
        "type": "vector",       // or "graph" or "fused"
        "file_id": "f7d8e9a0-...",
        "page": 12
      },
      "evidence": {
        "entities": ["產線A", "良率"],
        "relations": [{"from": "產線A", "rel": "has_issue", "to": "良率"}]
      }
    }
  ],
  "evidence_summary": {
    "total_chunks": 7,
    "total_graph_nodes": 3,
    "conflicts_detected": []
  },
  "took_ms": 234
}
```

**Errors**:
- `401` — missing/invalid API key
- `404` — KB not found or revoked
- `400` — `ontology_version` not available for this KB
- `503` — vector store or graph store unavailable

---

### `POST /api/v1/hybrid/evidence`

Same auth + request shape as `/hybrid/search` but **always returns evidence-only** (no full text chunks, just `chunk_id` + `text_excerpt` + `entities` + `relations`). Use when the caller has its own rendering and just needs raw material.

---

## Error Response Format

All errors use a consistent shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "human-readable summary",
    "details": {
      "field": "ontology_version",
      "issue": "must be a positive integer"
    },
    "trace_id": "tr_abc123"
  }
}
```

Standard error codes:
- `validation_error` (400)
- `unauthorized` (401)
- `forbidden` (403)
- `not_found` (404)
- `conflict` (409)
- `payload_too_large` (413)
- `rate_limited` (429)
- `internal_error` (500)
- `dependency_unavailable` (503)

The `trace_id` matches a structured log line; include it when reporting issues.

---

## Rate Limits

Defaults (configurable per deployment via reverse proxy):

| Endpoint class | Limit |
|---|---|
| `GET /health` | Unlimited |
| Admin endpoints | 10 req/min per IP |
| KB Key endpoints (read) | 100 req/min per API key |
| KB Key endpoints (write) | 20 req/min per API key |
| `POST /files` | 5 concurrent uploads per API key |

---

## Versioning

- **Path version**: `/api/v1` — breaking changes bump to `/v2`
- **Backward compatibility**: within a version, new optional fields may be added to responses; existing fields will not change type or be removed
- **Deprecation**: deprecated endpoints return `Deprecation` and `Sunset` headers (RFC 8594) at least 90 days before removal

---

## SDK / Client Libraries

Currently there is no official SDK. Clients are expected to use any HTTP client (curl, httpx, requests, fetch, etc.) and an OpenAPI generator (e.g., `openapi-generator-cli` for Python, `openapi-typescript` for TS).

A thin official `kag-client` Python package is on the roadmap but not v0.1.

---

## Keeping this doc in sync with the code

The table at the top of this file is auto-checked against the
running server. If you change `src/kag/api/**`, run:

```bash
uv run python scripts/check_api_sync.py --docs docs/API.md
```

In CI the same script runs on every PR; a non-zero exit fails the
build. The script compares three sources:

1. The FastAPI app's OpenAPI schema (canonical — generated from
   the live `app.openapi()`)
2. The endpoint table in this doc
3. The README's "External API" section

Adding a new endpoint is a **two-step** change — both must land in
the same commit:

1. Add the route under `src/kag/api/<topic>.py`. Use the existing
   `router = APIRouter(prefix="/api/v1/...", tags=["..."])` pattern.
   Tag it with one of the existing tag names from
   `src/kag/api/openapi.py` so the new endpoint shows up in the
   right group.
2. Add the row to the **Endpoints Overview** table at the top of
   this file. Format: ``| METHOD | `/path` | Auth | Description |``.

Renaming a path, changing a status code, or removing an endpoint
are all breaking changes — they require bumping the path version
(`/api/v1` → `/api/v2`) and the doc's Versioning section.

If you only want to add a new optional field to a response model,
you don't need to touch the table; the OpenAPI generator clients
will pick up the new field automatically.

### Ops endpoints (not part of the user API)

These are **not** included in the OpenAPI schema and don't appear
in the table above. They are documented here for operators.

| Method | Path | Description |
|---|---|---|
| GET | `/metrics` | Prometheus text-format scrape. Exposes `kag_http_requests_total{path,method,status}` and `kag_http_request_duration_ms_{count,sum}`. In-process counter, one per replica; aggregate at the Prometheus side. |
| GET | `/openapi.json` | Raw OpenAPI 3.1 schema. Mirror this in CI to detect doc drift (see `scripts/check_api_sync.py`). |
| GET | `/docs` | Swagger UI. |
| GET | `/redoc` | ReDoc UI. |
