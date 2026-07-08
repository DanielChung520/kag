"""Pipeline trigger endpoints + job status.

| Method | Path | Auth |
|---|---|---|
| POST | /api/v1/knowledge-bases/{kb_key}/pipelines/vectorize | KB Key |
| POST | /api/v1/knowledge-bases/{kb_key}/pipelines/extract-graph | KB Key |
| POST | /api/v1/knowledge-bases/{kb_key}/pipelines/all | KB Key |
| GET  | /api/v1/jobs/{job_id} | Admin OR KB Key (owning) |
| POST | /api/v1/jobs/{job_id}/abort | Admin OR KB Key (owning) |
| POST | /api/v1/jobs/{job_id}/retry | Admin OR KB Key (owning) |

All write-path actions return a list of Celery job IDs that the
client can poll via ``GET /jobs/{id}``. Celery task results are
mirrored back into the ArangoDB ``kag_jobs`` collection so the
HTTP layer can answer status queries without depending on Redis.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from celery.result import AsyncResult  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from kag.auth.dependencies import current_kb
from kag.db.arango import ArangoStore
from kag.models import Job, JobStatus, JobType, KnowledgeBase
from kag.store.kb import get_kb_store
from kag.tasks.celery_app import celery_app

log = structlog.get_logger("kag.api.pipelines")
router = APIRouter(tags=["pipelines"])


# ── Request / response schemas ────────────────────────────────────────


class TriggerResponse(BaseModel):
    kb_key: str
    pipeline: str
    job_ids: list[str]
    enqueued: int


class JobResponse(BaseModel):
    job_id: str
    type: JobType
    kb_key: str
    file_id: str | None
    status: JobStatus
    started_at: datetime | None
    finished_at: datetime | None
    error_msg: str | None
    created_at: datetime


# ── Helpers ───────────────────────────────────────────────────────────


def _persist_job(job: Job) -> None:
    """Upsert the job row into ArangoDB kag_jobs."""
    arango = ArangoStore()
    coll = arango.database.collection("kag_jobs")
    doc = {
        "_key": job.job_id,
        "type": str(job.type),
        "kb_key": job.kb_key,
        "file_id": job.file_id,
        "status": str(job.status),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_msg": job.error_msg,
        "log_tail": job.log_tail,
        "created_at": job.created_at.isoformat(),
    }
    try:
        coll.insert(doc)
    except Exception:
        coll.update(doc)


def _fetch_job(job_id: str) -> dict[str, Any] | None:
    arango = ArangoStore()
    return arango.query_one(
        "FOR j IN kag_jobs FILTER j._key == @jid RETURN j",
        bind_vars={"jid": job_id},
    )


def _job_row_to_response(row: dict[str, Any]) -> JobResponse:
    return JobResponse(
        job_id=row["_key"],
        type=JobType(row.get("type", "vectorize")),
        kb_key=row.get("kb_key", ""),
        file_id=row.get("file_id"),
        status=JobStatus(row.get("status", "pending")),
        started_at=(datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None),
        finished_at=(
            datetime.fromisoformat(row["finished_at"]) if row.get("finished_at") else None
        ),
        error_msg=row.get("error_msg"),
        created_at=(
            datetime.fromisoformat(row["created_at"])
            if row.get("created_at")
            else datetime.now(UTC)
        ),
    )


def _enqueue(pipeline: str, file_id: str, kb_key: str, ontology_name: str = "") -> tuple[str, Job]:
    """Enqueue a Celery task + persist a kag_jobs row, return (job_id, Job)."""
    job = Job(
        type=JobType.VECTORIZE if pipeline == "vectorize" else JobType.GRAPH_EXTRACT,
        kb_key=kb_key,
        file_id=file_id,
    )
    if pipeline == "vectorize":
        async_result = celery_app.send_task(
            "kag.tasks.vectorize.vectorize_task",
            args=[file_id, kb_key],
            task_id=job.job_id,
        )
    else:
        async_result = celery_app.send_task(
            "kag.tasks.graph.graph_task",
            args=[file_id, kb_key, ontology_name],
            task_id=job.job_id,
        )
    _persist_job(job)
    log.info(
        "pipeline.enqueued",
        pipeline=pipeline,
        job_id=job.job_id,
        file_id=file_id,
        kb_key=kb_key,
        async_id=async_result.id,
    )
    return job.job_id, job


def _list_files_by_status(kb_key: str, statuses: list[str]) -> list[dict[str, Any]]:
    arango = ArangoStore()
    return arango.query_all(
        """
        FOR f IN kag_files
          FILTER f.kb_key == @kb AND f.status IN @statuses
          RETURN f
        """,
        bind_vars={"kb": kb_key, "statuses": statuses},
    )


def _require_kb(kb_key: str, caller: KnowledgeBase) -> None:
    if caller.kb_key != kb_key or get_kb_store().get(kb_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


# ── Trigger endpoints ────────────────────────────────────────────────


@router.post(
    "/api/v1/knowledge-bases/{kb_key}/pipelines/vectorize",
    response_model=TriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_vectorize(
    kb_key: Annotated[str, Path(min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> TriggerResponse:
    _require_kb(kb_key, caller)
    pending = _list_files_by_status(kb_key, ["pending", "failed"])
    job_ids: list[str] = []
    for f in pending:
        fid = f.get("_key")
        if not fid:
            continue
        jid, _ = _enqueue("vectorize", fid, kb_key)
        job_ids.append(jid)
    log.info("vectorize.batch", kb_key=kb_key, enqueued=len(job_ids))
    return TriggerResponse(
        kb_key=kb_key, pipeline="vectorize", job_ids=job_ids, enqueued=len(job_ids)
    )


@router.post(
    "/api/v1/knowledge-bases/{kb_key}/pipelines/extract-graph",
    response_model=TriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_extract_graph(
    kb_key: Annotated[str, Path(min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
    ontology_name: Annotated[
        str,
        Path(description="Major-layer ontology name driving entity types."),
    ] = "",
) -> TriggerResponse:
    _require_kb(kb_key, caller)
    ready = _list_files_by_status(kb_key, ["vectorized", "graphed"])
    job_ids: list[str] = []
    for f in ready:
        fid = f.get("_key")
        if not fid:
            continue
        jid, _ = _enqueue("graph", fid, kb_key, ontology_name)
        job_ids.append(jid)
    log.info("graph.batch", kb_key=kb_key, enqueued=len(job_ids))
    return TriggerResponse(
        kb_key=kb_key, pipeline="extract-graph", job_ids=job_ids, enqueued=len(job_ids)
    )


@router.post(
    "/api/v1/knowledge-bases/{kb_key}/pipelines/all",
    response_model=TriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_all(
    kb_key: Annotated[str, Path(min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
    ontology_name: Annotated[
        str, Path(description="Major-layer ontology for graph extraction.")
    ] = "",
) -> TriggerResponse:
    """Vectorize all pending files then graph-extract everything that's ready."""
    v_resp = await trigger_vectorize(kb_key, caller)
    g_resp = await trigger_extract_graph(kb_key, caller, ontology_name)
    all_ids = v_resp.job_ids + g_resp.job_ids
    return TriggerResponse(kb_key=kb_key, pipeline="all", job_ids=all_ids, enqueued=len(all_ids))


# ── Task 28: job status / abort / retry ─────────────────────────────


@router.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: Annotated[str, Path(min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> JobResponse:
    row = _fetch_job(job_id)
    if row is None or row.get("kb_key") != caller.kb_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Cross-check with Celery's live state for the freshest status.
    try:
        result = AsyncResult(job_id, app=celery_app)
        live_status = result.status.lower()  # PENDING / STARTED / SUCCESS / FAILURE / REVOKED
    except Exception:
        live_status = None

    if live_status:
        # Normalize to our JobStatus enum values; REVOKED is a Celery-only state.
        mapping = {
            "pending": JobStatus.PENDING,
            "started": JobStatus.STARTED,
            "success": JobStatus.SUCCESS,
            "failure": JobStatus.FAILURE,
            "revoked": JobStatus.REVOKED,
        }
        if live_status in mapping:
            row["status"] = mapping[live_status].value
        # Capture started/finished timestamps
        if live_status == "started" and not row.get("started_at"):
            row["started_at"] = datetime.now(UTC).isoformat()
        if live_status in {"success", "failure", "revoked"} and not row.get("finished_at"):
            row["finished_at"] = datetime.now(UTC).isoformat()
            # Persist the transition so subsequent GETs see it.
            with contextlib.suppress(Exception):
                ArangoStore().database.collection("kag_jobs").update(
                    {
                        "_key": job_id,
                        "kb_key": row.get("kb_key", ""),
                        "status": row["status"],
                        "started_at": row.get("started_at"),
                        "finished_at": row["finished_at"],
                    }
                )

    return _job_row_to_response(row)


@router.post("/api/v1/jobs/{job_id}/abort", status_code=status.HTTP_204_NO_CONTENT)
async def abort_job(
    job_id: Annotated[str, Path(min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> None:
    row = _fetch_job(job_id)
    if row is None or row.get("kb_key") != caller.kb_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    try:
        celery_app.control.revoke(job_id, terminate=False)
    except Exception as exc:
        log.warning("job.revoke_failed", job_id=job_id, error=str(exc))
    try:
        ArangoStore().database.collection("kag_jobs").update(
            {
                "_key": job_id,
                "kb_key": row.get("kb_key", ""),
                "status": JobStatus.REVOKED.value,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:
        log.warning("job.revoke_persist_failed", job_id=job_id, error=str(exc))
    log.info("job.aborted", job_id=job_id)


@router.post("/api/v1/jobs/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: Annotated[str, Path(min_length=1)],
    caller: Annotated[KnowledgeBase, Depends(current_kb)],
) -> JobResponse:
    """Re-enqueue a failed/finished job with a new Celery task ID.

    The old job row stays as a historical record; we add a fresh
    job with a new id and link the new task back to the same
    ``file_id`` / ``kb_key``.
    """
    row = _fetch_job(job_id)
    if row is None or row.get("kb_key") != caller.kb_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    pipeline = "vectorize" if row.get("type") == JobType.VECTORIZE.value else "graph"
    file_id = row.get("file_id")
    if not file_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original job has no file_id; cannot retry",
        )
    new_id, _new_job = _enqueue(pipeline, file_id, row.get("kb_key", ""))
    return _job_row_to_response(_fetch_job(new_id) or {})
