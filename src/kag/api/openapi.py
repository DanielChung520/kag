"""OpenAPI customizations: security schemes, examples, tags, servers.

Wired into the FastAPI app via :func:`apply_openapi`. The base
``FastAPI(...)`` app is created in :mod:`kag.main`; this module
just provides a single function that mutates the OpenAPI schema
**after** all routes are registered (so it can pick up the actual
tag names, not guess them).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from kag import __version__

KAG_OPENAPI_DESCRIPTION = """\
kag — Knowledge-Augmented Generation service.

Two auth schemes, per-endpoint:

- **Admin Bearer token** (`Authorization: Bearer <KAG_ADMIN_TOKEN>`)
  for KB / ontology management.
- **Per-KB API key** (`X-KAG-API-Key: kag_xxx`) for file upload,
  search, pipeline trigger, hybrid search, and evidence.

The raw KB API key is returned exactly once at KB creation time
and never stored in plain text. Use the admin token for any
operation that touches a key other than the KB's own.
"""


_EXAMPLES: dict[str, dict[str, object]] = {
    "CreateKnowledgeBase": {
        "summary": "Create a KB and receive its one-time API key",
        "value": {
            "name": "Manufacturing Manuals",
            "description": "English + Chinese maintenance docs",
            "ontology_major_key": "manufacturing_v1",
            "ontology_version": 1,
        },
    },
    "UploadFileMultipart": {
        "summary": "Upload a PDF (multipart form-data)",
        "value": "(binary PDF body, field name 'file')",
    },
    "UploadFilePath": {
        "summary": "Upload via server-side file path (JSON)",
        "value": {"path": "/var/lib/kag/imports/manual.pdf"},
    },
    "HybridSearch": {
        "summary": "HybridRAG search (KB-scoped)",
        "value": {
            "query": "Which machines produce Product X?",
            "top_k": 10,
            "top_n": 5,
            "include_evidence": True,
        },
    },
}


def _security_schemes() -> dict[str, dict[str, object]]:
    return {
        "AdminBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
            "description": "Admin token from KAG_ADMIN_TOKEN (KB / ontology management).",
        },
        "KbApiKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-KAG-API-Key",
            "description": "Per-KB API key returned at KB creation time. Format: ``kag_<32 base62>``.",
        },
    }


def _tag_descriptions() -> list[dict[str, str]]:
    return [
        {"name": "health", "description": "Liveness probe."},
        {
            "name": "knowledge-bases",
            "description": "KB create / read / update / delete (admin). Per-KB detail readable by the owning key.",
        },
        {
            "name": "files",
            "description": "Upload, list, and fetch files within a KB. Requires the per-KB API key.",
        },
        {
            "name": "ontologies",
            "description": "CRUD + versioning + graph export for the three-layer ontology tree (admin).",
        },
        {
            "name": "pipelines",
            "description": "Trigger and observe Celery jobs (vectorize / graph extract). KB-keyed.",
        },
        {
            "name": "hybrid",
            "description": "HybridRAG (vector + graph fusion) queries, KB-scoped.",
        },
        {
            "name": "metrics",
            "description": "Prometheus scrape endpoint.",
        },
    ]


def apply_openapi(app: FastAPI) -> dict[str, object]:
    """Replace the default OpenAPI schema with our customized version.

    Call **after** all routers are included. FastAPI uses
    ``app.openapi_schema`` if set, so we replace it once.
    """
    base_schema = get_openapi(
        title="kag",
        version=__version__,
        description=KAG_OPENAPI_DESCRIPTION,
        routes=app.routes,
    )
    base_schema["components"]["securitySchemes"] = _security_schemes()
    base_schema["tags"] = _tag_descriptions()
    base_schema["servers"] = [
        {"url": "https://kag.aiconn.ai", "description": "Production (cloudflared)"},
        {"url": "http://localhost:8800", "description": "Local development"},
    ]
    # Per-endpoint examples — match operationId in FastAPI when we
    # add them; for now we attach the well-known operation ids.
    for ops in base_schema.get("paths", {}).values():
        for op in ops.values():
            op_id = op.get("operationId", "")
            ex_key = {
                "create_knowledge_base_ontology_post": "CreateKnowledgeBase",
                "create_ontology_ontologies_post": "CreateKnowledgeBase",
                "upload_file_knowledge_bases_kb_key_files_post": "UploadFileMultipart",
                "import_ontology_ontologies_import_post": "UploadFilePath",
                "hybrid_search_hybrid_search_post": "HybridSearch",
                "hybrid_evidence_hybrid_evidence_post": "HybridSearch",
            }.get(op_id)
            if ex_key and ex_key in _EXAMPLES:
                body = op.get("requestBody", {}).get("content", {})
                for media in body.values():
                    media.setdefault("examples", {})
                    media["examples"][ex_key] = _EXAMPLES[ex_key]
    app.openapi_schema = base_schema
    return base_schema
