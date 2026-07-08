# F4 — Scope Fidelity Check

**Date**: 2026-07-08
**Scope**: verify that **v0.x out-of-scope items** (per the original
plan and `docs/ARCHITECTURE.md`) are **NOT** implemented in the
codebase. The check is grep-based; each false positive is
explained.

## Out-of-scope items (per plan)

| Item | Should be | Actual | Verdict |
|---|---|---|---|
| Frontend / UI / HTML | absent | no `*.html` / `*.vue` / `*.css` / `*.jsx` files in repo | ✅ |
| User accounts / OAuth / sessions | absent | grep yields 0 hits for `oauth` / `session` (the 3 false positives are: a docstring in `openapi.py` mentioning "after all routes are registered", `start_new_session=True` on the Celery `Popen` in `cli.py` (subprocess), and a docstring in `tasks/__init__.py`) | ✅ |
| Cross-KB search | absent | grep yields 1 hit in `kb.py:7` for `Admin OR matching KB key` — that is the **OR-within-a-single-KB** check on the detail endpoint, not cross-KB | ✅ |
| Streaming responses (SSE / WebSocket) | absent | grep yields 0 hits; all endpoints are `def` / `async def` returning a Pydantic model | ✅ |
| Webhooks / event subscriptions | absent | grep yields 0 hits | ✅ |
| Non-OpenAI LLM providers (Ollama, Anthropic, Cohere, Gemini, Bedrock) | absent | grep yields 0 hits; the only LLM client is the `openai.AsyncOpenAI` SDK which is pointed at `dllm` / vLLM / llama.cpp (all OpenAI-compatible) | ✅ |
| Non-Qdrant vector stores (Weaviate, Milvus, Chroma, Pinecone) | absent | grep yields 0 hits; only `qdrant_client` and `kag.db.qdrant` are present | ✅ |
| Non-ArangoDB graph stores (Neo4j, TigerGraph, JanusGraph) | absent | grep yields 0 hits; only `python_arango` and `kag.db.arango` are present | ✅ |
| `kag_ontology_evolution` collection (Hippocampus interface) | absent (out of v0.x) | 0 hits in `src/`; only mentioned in `docs/API.md` and `docs/DATA_MODEL.md` (those were present at session start as pre-existing files, not added by us) | ✅ |

## Default config that LOOKS like hardcoding but is not

| Line | Code | Why it's fine |
|---|---|---|
| `config.py:76` | `QDRANT_VECTOR_DIM: int = 1024` | A configurable default; operator overrides via env var. The literal `1024` is the bge-m3 output dim documented in `docs/ARCHITECTURE.md`. |
| `config.py:102` | `LLM_MAX_TOKENS_VL: int = 1024` | Same — a default. |

Both are tunable via env, not architectural commitments.

## Cross-references

- The plan's "Out of Scope (v0.x)" list (`docs/ARCHITECTURE.md` §
  "Out of Scope", plus the `kag-standalone.md` plan file) is
  identical to the items checked above.
- Items that **were** in scope and are implemented as documented:
  HybridRAG, per-KB API keys, Celery pipeline, ontology
  versioning, structured chunker, ontology-aware graph
  extraction, deduplication, conflict detection.

## Conclusion

No v0.x out-of-scope features leaked into the implementation.
The codebase matches the plan's scope envelope exactly.
