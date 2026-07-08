# kag — Data Model

> All data kag owns is namespaced: ArangoDB collections start with `kag_`, Qdrant collections start with `kag_kb_`, SeaweedFS keys live under `kag/`.

---

## ArangoDB Collections

ArangoDB is the **source of truth** for: KB metadata, file metadata, ontologies, jobs, API key hashes, and the **knowledge graph** (extracted entities + relations). It also stores chunk text in the rare case Qdrant loses its payload.

All collections are created by `kag migrate` with appropriate indexes. The DB is shared with `aibox-th`; kag must NEVER read or write non-`kag_` collections.

### `kag_knowledge_bases`

| Field | Type | Description |
|---|---|---|
| `_key` | string (UUID4) | KB identifier (the `kb_key` exposed via API) |
| `name` | string | Human-readable name |
| `description` | string \| null | Optional |
| `ontology_major` | string | FK to `kag_ontology._key` (must be `layer=major`) |
| `ontology_version` | int | Pinned ontology version at KB creation |
| `status` | string | `active` \| `archived` \| `deleting` |
| `created_at` | ISO datetime | |
| `updated_at` | ISO datetime | |
| `created_by` | string | Admin token id (for audit) |
| `file_count` | int | Cache for fast list (recomputed on file change) |
| `chunk_count` | int | Cache (recomputed on vectorize) |
| `graph_node_count` | int | Cache (recomputed on graph extract) |

**Indexes**:
- `name` (hash, unique) — for "does name exist?" check
- `status` (hash)
- `created_at` (skiplist, descending) — for listing

### `kag_api_keys`

| Field | Type | Description |
|---|---|---|
| `_key` | string (UUID4) | Internal key id (NOT the API key itself) |
| `kb_key` | string | FK to `kag_knowledge_bases._key` |
| `key_hash` | string | `sha256(api_key + KAG_API_KEY_PEPPER)` |
| `label` | string | Human label like "default" or "ci-runner-2026-07" |
| `created_at` | ISO datetime | |
| `last_used_at` | ISO datetime \| null | |
| `revoked_at` | ISO datetime \| null | Soft revoke; `null` = active |

**Indexes**:
- `key_hash` (hash, unique) — primary lookup
- `kb_key, revoked_at` (skiplist) — for "list active keys for KB"

**Cap**: at most 5 active keys per KB. Creating a 6th revokes the oldest (configurable: `KAG_API_KEY_MAX_ACTIVE_PER_KB`).

### `kag_files`

| Field | Type | Description |
|---|---|---|
| `_key` | string (UUID4) | File id |
| `kb_key` | string | FK |
| `filename` | string | Original filename |
| `mime_type` | string | Detected MIME |
| `size_bytes` | int | |
| `seaweed_key` | string | Path in SeaweedFS |
| `status` | string | `pending` \| `vectorizing` \| `vectorized` \| `graphing` \| `graphed` \| `failed` |
| `vector_status` | string | Sub-state of vectorization |
| `graph_status` | string | Sub-state of graph extraction |
| `error_msg` | string \| null | Last error if failed |
| `uploaded_at` | ISO datetime | |
| `processed_at` | ISO datetime \| null | When last pipeline finished successfully |
| `metadata` | object | User-supplied arbitrary key/value |
| `content_hash` | string | SHA256 of original bytes (for dedup) |
| `page_count` | int \| null | For PDFs |
| `chunk_count` | int | Computed after vectorize |

**Indexes**:
- `kb_key, status` (skiplist)
- `kb_key, uploaded_at` (skiplist, descending)
- `content_hash` (hash) — for dedup (future)

### `kag_chunks`

Chunk text + metadata. The vector itself lives in Qdrant; this collection provides the text, metadata, and a back-up if Qdrant is unavailable.

| Field | Type | Description |
|---|---|---|
| `_key` | string | `{file_id}:{position}` |
| `kb_key` | string | FK |
| `file_id` | string | FK |
| `position` | int | 0-indexed order in file |
| `text` | string | Chunk text (≤ 8192 chars; larger chunks are truncated) |
| `token_count` | int | |
| `meta` | object | `{page, heading, image_caption?, ...}` |
| `created_at` | ISO datetime | |

**Indexes**:
- `file_id, position` (skiplist) — for ordered retrieval
- `kb_key` (hash) — for KB-wide queries
- `meta.heading` (hash, optional) — for heading-based search (v0.2)

### `kag_graph_nodes`

Entities extracted from chunks.

| Field | Type | Description |
|---|---|---|
| `_key` | string | Normalized entity key (`{type}:{normalized_name}`) — enables dedup across files |
| `kb_key` | string | FK |
| `entity_type` | string | e.g., `Person`, `Machine`, `Process` (from ontology) |
| `canonical_name` | string | Display name |
| `aliases` | string[] | All surface forms seen across files |
| `source_file_ids` | string[] | Files where this entity was extracted |
| `properties` | object | Type-specific properties from ontology |
| `first_seen_at` | ISO datetime | |
| `updated_at` | ISO datetime | |

**Indexes**:
- `kb_key, entity_type` (skiplist)
- `kb_key, canonical_name` (hash) — exact lookup
- `aliases` (hash) — alias lookup (may be expensive; consider fulltext index in v0.2)

### `kag_graph_edges`

Relations between entities.

| Field | Type | Description |
|---|---|---|
| `_key` | string | `{from_key}->{rel_type}->{to_key}` — dedup |
| `_from` | string | Arango edge: `kag_graph_nodes/{from_key}` |
| `_to` | string | Arango edge: `kag_graph_nodes/{to_key}` |
| `rel_type` | string | From ontology's `object_properties` |
| `kb_key` | string | FK |
| `source_chunk_ids` | string[] | Which chunks mentioned this relation |
| `properties` | object | Relation properties (e.g., `since: "2024-01"`) |
| `weight` | float | Confidence × source count (used in ranking) |
| `created_at` | ISO datetime | |
| `updated_at` | ISO datetime | |

This is an **edge collection** (note the `_from` / `_to` fields). Use `graph_traversal` queries for paths.

### `kag_ontology`

| Field | Type | Description |
|---|---|---|
| `_key` | string | `{layer}/{name}` (e.g., `major/manufacturing_v1`) |
| `layer` | string | `basic` \| `domain` \| `major` |
| `name` | string | Ontology name (unique per layer) |
| `description` | string | |
| `inherits_from` | string \| null | FK to `kag_ontology._key` (only for `domain` and `major`) |
| `current_version` | int | Latest version |
| `status` | string | `draft` \| `published` \| `deprecated` |
| `created_at` | ISO datetime | |
| `updated_at` | ISO datetime | |

### `kag_ontology_version`

Immutable history of ontology versions.

| Field | Type | Description |
|---|---|---|
| `_key` | string | `{layer}/{name}/v{version}` |
| `ontology_key` | string | FK to `kag_ontology._key` |
| `version` | int | Monotonically increasing per ontology |
| `payload` | object | Full ontology body: `{entity_classes, object_properties, use_cases, tags, ...}` |
| `diff_from_previous` | object \| null | `{added: [...], removed: [...], changed: [...]}` |
| `created_at` | ISO datetime | |
| `created_by` | string | Admin token id |

**Indexes**:
- `ontology_key, version` (skiplist, descending) — latest version lookup
- `ontology_key` (hash)

### `kag_jobs`

Celery task tracking.

| Field | Type | Description |
|---|---|---|
| `_key` | string | Celery task id |
| `type` | string | `vectorize` \| `graph_extract` \| `vlm_caption` |
| `kb_key` | string | FK |
| `file_id` | string \| null | FK (null for KB-wide jobs) |
| `status` | string | `pending` \| `started` \| `success` \| `failure` \| `revoked` |
| `enqueued_at` | ISO datetime | |
| `started_at` | ISO datetime \| null | |
| `finished_at` | ISO datetime \| null | |
| `result` | object \| null | Summary (e.g., `{chunks_written: 42}`) |
| `error` | string \| null | Error message if failed |
| `traceback` | string \| null | Last N lines of Python traceback |
| `log_tail` | string[] | Last 50 INFO/ERROR log lines (capped) |
| `retry_count` | int | |

**Indexes**:
- `kb_key, enqueued_at` (skiplist, descending) — for KB history
- `file_id, type` (hash) — "what jobs ran for this file?"
- `status` (hash) — for "any pending jobs?"

### `kag_schema`

Single document tracking schema version (for migrations).

| Field | Type | Description |
|---|---|---|
| `_key` | string | Always `singleton` |
| `version` | int | Current schema version |
| `applied_at` | ISO datetime | |
| `applied_migrations` | string[] | List of migration ids applied, in order |

---

## Qdrant Collections

Qdrant holds **vector embeddings only**. Text is in `kag_chunks`; metadata in Qdrant payload.

### `kag_kb_{kb_key}` (one per KB)

**Vector config**:
- `size`: `QDRANT_VECTOR_DIM` (default 1024, must match `EMBEDDING_MODEL`'s output dim — e.g., `bge-m3` produces 1024)
- `distance`: `Cosine`

**Payload schema** (per point):
```json
{
  "kb_key": "...",
  "file_id": "...",
  "chunk_id": "...",
  "position": 0,
  "text_excerpt": "first 200 chars of chunk",
  "meta": { "page": 12, "heading": "..." }
}
```

**Payload indexes** (for filtering):
- `kb_key` (keyword)
- `file_id` (keyword)
- `meta.heading` (keyword) — v0.2

**Lifecycle**:
- Created on first vectorize_task for a new KB
- Deleted when KB is deleted
- No re-creation needed on vector model change (re-vectorize creates new points; old points are deleted by the same task)
- **Embedding model change** (e.g., switching from `bge-m3` 1024-dim to a 1536-dim model): the collection must be dropped and recreated. This is handled by an admin script `kag reindex {kb_key}` (planned v0.2)

---

## SeaweedFS

S3-compatible object storage. Single bucket `kag`.

### Key layout

```
kag/
├── {kb_key}/
│   ├── {file_id}/
│   │   ├── original              # original uploaded file
│   │   ├── chunks.jsonl          # extracted chunks (debug + replay)
│   │   ├── graph.jsonl           # extracted entities/relations (debug)
│   │   └── vlm_cache/            # cached VL captions
│   │       └── {block_id}.json
│   └── ...
```

### Lifecycle

- Original files: retained while KB exists
- `chunks.jsonl`, `graph.jsonl`, `vlm_cache/`: retained 30 days after `processed_at`, then eligible for GC (separate background job, v0.2)

---

## Redis

Used only as Celery broker + result backend. No application state stored in Redis directly.

Key naming:
- `kag:celery:task-meta-{task_id}` — Celery internal
- `kag:celery:queue` — Celery internal

No kag-specific keys (yet). v0.2 may add rate-limit counters here.

---

## Migration Strategy

Schema changes are managed by **versioned migration files**:

```
src/kag/migrations/
├── 0001_initial.py
├── 0002_add_chunk_meta_index.py
├── 0003_...
```

Each migration has `up(arango: ArangoStore, qdrant: QdrantStore, seaweeed: SeaweedStore) -> None` and `down(...) -> None`.

`kag migrate` runs all unapplied migrations in order and updates `kag_schema.applied_migrations`. **Migrations are idempotent** (safe to re-run).

---

## Capacity Planning (rough estimates)

Per 1000 files of average 10 pages each (≈ 5MB PDF):

| Resource | Estimate |
|---|---|
| ArangoDB | ~2M nodes, ~5M edges, ~5M chunks → 20GB disk, 4GB RAM |
| Qdrant | 5M vectors × 1024 dim × 4 bytes = 20GB vectors + 2GB payload → 22GB disk, 4GB RAM |
| SeaweedFS | 5GB originals + 1GB metadata = 6GB |
| Redis | < 100MB (just Celery) |

Rule of thumb: **~50GB disk and 8GB RAM per 1000 files**, dominated by Qdrant and ArangoDB.
