from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.database.models.approval_requests import ApprovalRequest
from app.database.models.run_steps import RunStep
from app.database.models.tool_calls import ToolCall


class ApprovalRepository:
    @staticmethod
    def create_request(
        db: Session,
        run_id: uuid.UUID,
        tool_call_id: uuid.UUID,
        expires_in_seconds: int = 1800,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            id=uuid.uuid4(),
            run_id=run_id,
            tool_call_id=tool_call_id,
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return approval

    @staticmethod
    def get_request(
        db: Session,
        approval_id: uuid.UUID,
    ) -> ApprovalRequest | None:
        return db.get(ApprovalRequest, approval_id)

    @staticmethod
    def get_pending_requests(db: Session) -> list[ApprovalRequest]:
        now = datetime.now(UTC)
        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.status == "pending",
                or_(ApprovalRequest.expires_at.is_(None), ApprovalRequest.expires_at > now),
            )
            .order_by(ApprovalRequest.requested_at.asc())
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def get_pending_request_details(
        db: Session,
    ) -> list[tuple[ApprovalRequest, ToolCall, RunStep | None]]:
        now = datetime.now(UTC)
        statement = (
            select(ApprovalRequest, ToolCall, RunStep)
            .join(ToolCall, ToolCall.id == ApprovalRequest.tool_call_id)
            .outerjoin(RunStep, RunStep.id == ToolCall.run_step_id)
            .where(
                ApprovalRequest.status == "pending",
                or_(ApprovalRequest.expires_at.is_(None), ApprovalRequest.expires_at > now),
            )
            .order_by(ApprovalRequest.requested_at.asc())
        )
        return [(request, tool_call, step) for request, tool_call, step in db.execute(statement).all()]

    @staticmethod
    def expire_pending_requests(
        db: Session,
        now: datetime | None = None,
        commit: bool = True,
        run_id: uuid.UUID | None = None,
    ) -> int:
        reference_time = now or datetime.now(UTC)
        filters = [
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at.is_not(None),
            ApprovalRequest.expires_at <= reference_time,
        ]
        if run_id is not None:
            filters.append(ApprovalRequest.run_id == run_id)
        result = db.execute(
            update(ApprovalRequest)
            .where(*filters)
            .values(status="expired", updated_at=reference_time)
        )
        if commit:
            db.commit()
        return result.rowcount or 0

    @staticmethod
    def get_requests_by_run(
        db: Session,
        run_id: uuid.UUID,
    ) -> list[ApprovalRequest]:
        statement = (
            select(ApprovalRequest)
            .where(ApprovalRequest.run_id == run_id)
            .order_by(ApprovalRequest.requested_at.asc())
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def resolve_request(
        db: Session,
        approval_id: uuid.UUID,
        status: str,
    ) -> ApprovalRequest | None:
        now = datetime.now(UTC)
        approval = db.get(ApprovalRequest, approval_id)
        if approval is None or approval.status != "pending":
            return None
        if approval.expires_at is not None and approval.expires_at <= now:
            approval.status = "expired"
            db.commit()
            return None

        statement = (
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.status == "pending",
                or_(ApprovalRequest.expires_at.is_(None), ApprovalRequest.expires_at > now),
            )
            .values(
                status=status,
                resolved_at=now,
                updated_at=now,
            )
            .returning(ApprovalRequest)
        )
        approval = db.scalar(statement)
        db.commit()
        return approval

    @staticmethod
    def cancel_pending_by_run(
        db: Session,
        run_id: uuid.UUID,
        commit: bool = True,
    ) -> int:
        now = datetime.now(UTC)
        result = db.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.run_id == run_id,
                ApprovalRequest.status == "pending",
            )
            .values(
                status="cancelled",
                resolved_at=now,
                updated_at=now,
            )
        )
        if commit:
            db.commit()
        return result.rowcount or 0
