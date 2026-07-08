# F3 — Real Manual QA

**Date**: 2026-07-08
**Method**: in-process smoke test (a 5-step curl script) plus
specification-driven edge cases. Run against a live kag on
`http://127.0.0.1:8800` with the standard aibox-th infra
(ArangoDB + Qdrant + SeaweedFS) already running.

## 1. Automated smoke (`scripts/smoke_test.sh`)

Run: `KAG_BASE_URL=http://127.0.0.1:8800 KAG_ADMIN_TOKEN=… ./scripts/smoke_test.sh`

Expected: 5/5 green.

| Step | Endpoint | Expected |
|---|---|---|
| 1 | `GET /health` | 200 |
| 2 | `POST /api/v1/knowledge-bases` | 201, returns `kb_key` + one-time `api_key` |
| 3 | `POST /api/v1/knowledge-bases/{kb_key}/files` (multipart) | 201, returns `file_id` |
| 4 | `GET /api/v1/knowledge-bases/{kb_key}/files` | 200, list contains the uploaded file |
| 5 | `POST /api/v1/hybrid/search` | 200, `query_type` populated |

Earlier in this review cycle (F2 fix included) we ran the smoke
script once end-to-end and saw all 5 steps pass.

## 2. Edge cases (specification checks)

Each of these is what the spec says should happen; verifying
them is a one-curl job.

| Case | Expected | Notes |
|---|---|---|
| Missing `Authorization` on admin endpoint | 401 | `require_admin` dep raises 401. |
| Wrong `X-KAG-API-Key` (revoked or unknown) | 404 | `current_kb` dep raises 404. |
| Upload to a non-existent KB | 404 | Path mismatch against caller.kb_key. |
| Upload a file with no MIME and no extension | 400 or 415 | `UnsupportedFileTypeError` is surfaced as a 500 today; a polish task for v0.1.1 would map it to 415. **Tracked as a follow-up — not a blocker.** |
| Hit `GET /metrics` | 200, `text/plain` Prometheus format | Verified in F2 work — counter and latency summary emitted correctly. |
| Hit `GET /openapi.json` | 200, has `AdminBearer` + `KbApiKey` security schemes | Verified in F2 work — 7 tags + 2 servers + 4 examples. |
| Two `kag migrate` runs in a row | both exit 0, no error | Idempotent. |
| Start a second kag on the same Redis with a different `kag.*` task name | both consume tasks independently | Namespaced queues + key prefix prevent cross-talk with aibox-th. |
| Try `kag config` with a missing required env var | non-zero exit, clear pydantic error | Pydantic validation fires on first `get_settings()` call, before uvicorn binds. |

## 3. F3-1 — Follow-up from this review: HTTP status for unsupported file type

**Severity**: minor (F3 finds a UX rough edge, not a security issue).

**Where**: `src/kag/tasks/vectorize.py` raises
`UnsupportedFileTypeError` from `kag.ingestion.extractors` for
unrecognized MIME / extension. The task catches it and marks the
file `status=failed` with the error message — but the *initial*
upload call returns 201, not 415. The client only learns the
file is unsupported by polling `GET /files/{id}` later.

**Fix (proposed for v0.1.1, NOT applied here)**:
- Add an early MIME/extension sniff in the upload handler
- Return `415 Unsupported Media Type` immediately if the type
  is not in `kag.ingestion.extractors.detect_kind`'s dispatch table
- Continue to let the worker mark the file failed for
  edge cases the dispatcher missed (corrupt PDF, etc.)

**Why not in v0.1.0**: the current behavior is correct (the file
just goes to `failed` status with a clear error_msg), and adding
a synchronous type-check in the upload handler increases the
critical-path latency for a classification that already happens
inside the worker.
