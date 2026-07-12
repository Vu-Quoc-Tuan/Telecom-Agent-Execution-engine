from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_llm_gateway
from app.database.connection import SessionLocal, get_db
from app.services.agent_execution import AgentExecutionService
from app.services.approval import ApprovalService
from app.streaming.background import shielded_stream
from app.streaming.event_mapper import TelecomStreamEventMapper
from app.streaming.sse import format_sse_event

router = APIRouter()


class ResolveApprovalBody(BaseModel):
    action: str


@router.get("/pending")
def list_pending_approvals(db: Session = Depends(get_db)):
    """Hiển thị danh sách các lệnh nguy hiểm đang chờ phê duyệt."""
    return ApprovalService.list_pending_approval_details_for_ui(db)


@router.get("/{approval_id}")
def get_approval_detail(approval_id: uuid.UUID, db: Session = Depends(get_db)):
    """Lấy chi tiết một hộp duyệt để UI render Approval Card."""
    detail = ApprovalService.get_approval_detail_for_ui(db, approval_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Approval request không tồn tại.")
    return detail


@router.post("/{approval_id}/resolve")
async def resolve_and_resume(
    approval_id: uuid.UUID,
    body: ResolveApprovalBody,
):
    # DB session sống xuyên suốt agent resume: đặt bên trong _agent_generator
    # (chạy trong background task) để browser disconnect không huỷ execution.
    async def _agent_generator():
        with SessionLocal() as db:
            async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                db=db,
                llm_gateway=get_llm_gateway(),
                approval_id=approval_id,
                action=body.action,
            ):
                yield event

    async def sse_pipeline_transport():
        async for event_type, payload in shielded_stream(_agent_generator()):
            envelope = TelecomStreamEventMapper.map_raw_payload_to_envelope(event_type, payload)
            yield format_sse_event(envelope.event_type.value, envelope.payload.model_dump())

    return StreamingResponse(sse_pipeline_transport(), media_type="text/event-stream")
