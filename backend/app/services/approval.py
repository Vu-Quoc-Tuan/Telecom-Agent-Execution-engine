from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.database.models.approval_requests import ApprovalRequest
from app.database.repositories.approvals import ApprovalRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.tool_calls import ToolCallRepository


class ApprovalService:
    @staticmethod
    def get_approval_detail_for_ui(db: Session, approval_id: uuid.UUID) -> dict[str, Any] | None:
        req = ApprovalRepository.get_request(db, approval_id)
        if not req:
            return None

        tool_call = ToolCallRepository.get_tool_call(db, req.tool_call_id)
        step = RunStepRepository.get_step(db, tool_call.run_step_id) if tool_call else None

        timeline_step = None
        if step:
            timeline_step = {
                "id": str(step.id),
                "name": step.name,
                "status": step.status,
            }

        skill_details = None
        if tool_call:
            skill_details = {
                "skill_name": tool_call.skill_name,
                "arguments": tool_call.arguments_json,
                "connector_name": tool_call.connector_name,
                "risk_level": tool_call.risk_level,
            }

        return {
            "approval_id": str(req.id),
            "run_id": str(req.run_id),
            "status": req.status,
            "reason": req.reason,
            "requested_at": req.requested_at.isoformat(),
            "expires_at": req.expires_at.isoformat() if req.expires_at else None,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
            "resolved_by": req.resolved_by,
            "resolution_note": req.resolution_note,
            "timeline_step": timeline_step,
            "skill_details": skill_details,
        }

    @staticmethod
    def list_all_pending_requests(db: Session) -> list[ApprovalRequest]:
        return ApprovalRepository.get_pending_requests(db)

    @staticmethod
    def list_requests_by_agent_run(db: Session, run_id: uuid.UUID) -> list[ApprovalRequest]:
        return ApprovalRepository.get_requests_by_run(db, run_id)
