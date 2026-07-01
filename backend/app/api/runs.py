from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.enums import RunStatus
from app.config import settings
from app.database.connection import get_db
from app.database.repositories.runs import RunRepository
from app.services.runs import RunLifecycleService
from app.services.timeline import serialize_timeline_steps

router = APIRouter()


class CancelRunBody(BaseModel):
    reason: str | None = None
    requested_by: str = "operator_admin"


class QueueInterventionBody(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    requested_by: str = "operator_admin"


class MarkTimedOutRunsBody(BaseModel):
    timeout_seconds: int | None = Field(default=None, gt=0)
    limit: int = Field(default=100, gt=0, le=1000)


def _serialize_run(run_record, *, changed: bool | None = None) -> dict:
    payload = {
        "run_id": str(run_record.id),
        "status": run_record.status,
        "completed_at": run_record.completed_at.isoformat() if run_record.completed_at else None,
        "error_message": run_record.error_message,
    }
    if changed is not None:
        payload["changed"] = changed
    return payload


@router.post("/mark-timeouts")
def mark_timed_out_runs(
    body: MarkTimedOutRunsBody | None = None,
    db: Session = Depends(get_db),
):
    request = body or MarkTimedOutRunsBody()
    timeout_seconds = request.timeout_seconds or settings.RUN_TIMEOUT_SECONDS
    timed_out_runs = RunLifecycleService.mark_timed_out_runs(
        db=db,
        timeout_seconds=timeout_seconds,
        limit=request.limit,
    )
    return {
        "status": "ok",
        "timeout_seconds": timeout_seconds,
        "timed_out_count": len(timed_out_runs),
        "runs": [_serialize_run(run_record) for run_record in timed_out_runs],
    }


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: uuid.UUID,
    body: CancelRunBody | None = None,
    db: Session = Depends(get_db),
):
    request = body or CancelRunBody()
    result = RunLifecycleService.cancel_run(
        db=db,
        run_id=run_id,
        requested_by=request.requested_by,
        reason=request.reason,
    )
    if result.run is None:
        raise HTTPException(status_code=404, detail="Lượt chạy không tồn tại.")
    if not result.changed and result.run.status != RunStatus.CANCELLED.value:
        raise HTTPException(status_code=409, detail=result.message)

    return {
        **_serialize_run(result.run, changed=result.changed),
        "message": result.message,
    }


@router.post("/{run_id}/interventions")
def queue_run_intervention(
    run_id: uuid.UUID,
    body: QueueInterventionBody,
    db: Session = Depends(get_db),
):
    message = RunLifecycleService.queue_intervention(
        db=db,
        run_id=run_id,
        content=body.content,
        requested_by=body.requested_by,
    )
    if message is None:
        raise HTTPException(status_code=409, detail="Run không còn ở trạng thái nhận can thiệp.")

    return {
        "id": str(message.id),
        "session_id": str(message.session_id),
        "run_id": str(message.run_id) if message.run_id else None,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "sequence_no": message.sequence_no,
        "metadata": message.metadata_json,
        "created_at": message.created_at.isoformat(),
    }


@router.get("/{run_id}/timeline")
def get_run_timeline_steps(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """Frontend gọi API này khi kỹ sư bấm f5 lại trang hoặc chuyển session để vẽ lại Timeline cột phải"""
    run_record = RunRepository.get_run(db, run_id)
    if not run_record:
        raise HTTPException(status_code=404, detail="Lượt chạy không tồn tại.")

    return {
        "run_id": str(run_record.id),
        "status": run_record.status,
        "model": run_record.model,
        "steps": serialize_timeline_steps(db, run_id),
    }
