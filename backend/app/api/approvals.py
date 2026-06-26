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
from app.streaming.event_mapper import TelecomStreamEventMapper
from app.streaming.sse import format_sse_event

router = APIRouter()


class ResolveApprovalBody(BaseModel):
    action: str
    note: str | None = None


@router.get("/pending")
def list_pending_approvals(db: Session = Depends(get_db)):
    """Admin Dashboard bốc danh sách các lệnh nguy hiểm đang xếp hàng chờ phê duyệt."""
    pending = ApprovalService.list_all_pending_requests(db)
    return [ApprovalService.get_approval_detail_for_ui(db, req.id) for req in pending]


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
    # DB session sống xuyên suốt stream resume: mở trong generator thay vì Depends(get_db),
    # vì session từ Depends bị FastAPI đóng ngay khi trả về StreamingResponse.
    async def sse_pipeline_transport():
        with SessionLocal() as db:
            raw_generator = AgentExecutionService.resolve_approval_and_resume_lifecycle(
                db=db,
                llm_gateway=get_llm_gateway(),
                approval_id=approval_id,
                action=body.action,
                resolved_by="operator_admin",
                note=body.note,
            )
            async for event_type, payload in raw_generator:
                envelope = TelecomStreamEventMapper.map_raw_payload_to_envelope(event_type, payload)
                yield format_sse_event(envelope.event_type.value, envelope.payload.model_dump())

    return StreamingResponse(sse_pipeline_transport(), media_type="text/event-stream")
