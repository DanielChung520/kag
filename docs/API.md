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

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Health + dependency status |
| POST | `/api/v1/knowledge-bases` | Admin | Create KB (returns `api_key` once) |
| GET | `/api/v1/knowledge-bases` | Admin | List all KBs |
| GET | `/api/v1/knowledge-bases/{kb_key}` | Admin OR KB Key | Get KB detail |
| PATCH | `/api/v1/knowledge-bases/{kb_key}` | Admin | Update name/description |
| DELETE | `/api/v1/knowledge-bases/{kb_key}` | Admin | Full cleanup |
| POST | `/api/v1/knowledge-bases/{kb_key}/api-keys` | Admin | Issue new key (rotates old) |
| GET | `/api/v1/knowledge-bases/{kb_key}/api-keys` | Admin | List active keys (no raw values) |
| DELETE | `/api/v1/knowledge-bases/{kb_key}/api-keys/{key_id}` | Admin | Revoke key |
| POST | `/api/v1/knowledge-bases/{kb_key}/files` | KB Key | Upload file (multipart OR path) |
| GET | `/api/v1/knowledge-bases/{kb_key}/files` | KB Key | List files |
| GET | `/api/v1/knowledge-bases/{kb_key}/files/{file_id}` | KB Key | File detail + status |
| DELETE | `/api/v1/knowledge-bases/{kb_key}/files/{file_id}` | KB Key | Delete file + all derived data |
| GET | `/api/v1/knowledge-bases/{kb_key}/files/{file_id}/download` | KB Key | Presigned URL to original |
| POST | `/api/v1/knowledge-bases/{kb_key}/pipelines/vectorize` | KB Key | Trigger vectorization for files |
| POST | `/api/v1/knowledge-bases/{kb_key}/pipelines/extract-graph` | KB Key | Trigger graph extraction |
| POST | `/api/v1/knowledge-bases/{kb_key}/pipelines/all` | KB Key | Run full pipeline |
| GET | `/api/v1/jobs/{job_id}` | Admin OR KB Key (owning) | Job status |
| POST | `/api/v1/jobs/{job_id}/abort` | Admin OR KB Key | Revoke task |
| POST | `/api/v1/jobs/{job_id}/retry` | Admin OR KB Key | Re-enqueue task |
| GET | `/api/v1/jobs/{job_id}/logs` | Admin OR KB Key | Last N log lines |
| POST | `/api/v1/ontologies` | Admin | Create ontology |
| POST | `/api/v1/ontologies/import` | Admin | Import ontology JSON (file OR inline) |
| GET | `/api/v1/ontologies` | Admin | List ontologies |
| GET | `/api/v1/ontologies/{key}` | Admin | Get latest version |
| GET | `/api/v1/ontologies/{key}/versions` | Admin | List all versions |
| GET | `/api/v1/ontologies/{key}/versions/{v}` | Admin | Get specific version |
| PUT | `/api/v1/ontologies/{key}` | Admin | Update (bumps version) |
| DELETE | `/api/v1/ontologies/{key}` | Admin | Soft delete (status=deprecated) |
| GET | `/api/v1/ontologies/{key}/graph` | Admin | Export nodes/edges for viz |
| POST | `/api/v1/hybrid/search` | KB Key | HybridRAG (vector + graph fusion) |
| POST | `/api/v1/hybrid/evidence` | KB Key | Evidence-only search |
| POST | `/api/v1/vectors/search` | KB Key | Pure vector search (faster, less complete) |

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

### `POST /api/v1/vectors/search`

Pure vector search, no graph fusion. Faster, simpler. Same auth + filters as hybrid.

**Response**: like `/hybrid/search` but `source.type` is always `"vector"`, no `evidence` field, no `evidence_summary`.

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
