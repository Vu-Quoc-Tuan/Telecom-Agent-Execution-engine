from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.database.models.approval_requests import ApprovalRequest


class ApprovalRepository:
    @staticmethod
    def create_request(
        db: Session,
        run_id: uuid.UUID,
        tool_call_id: uuid.UUID,
        expires_in_seconds: int = 1800,
        required_confirmations: int = 1,
    ) -> ApprovalRequest:
        if required_confirmations not in {1, 2}:
            raise ValueError("required_confirmations must be 1 or 2")
        approval = ApprovalRequest(
            id=uuid.uuid4(),
            run_id=run_id,
            tool_call_id=tool_call_id,
            status="pending",
            required_confirmations=required_confirmations,
            confirmation_count=0,
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
        db.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.status == "pending",
                ApprovalRequest.expires_at.is_not(None),
                ApprovalRequest.expires_at <= now,
            )
            .values(status="expired", updated_at=now)
        )
        db.commit()
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

        next_count = approval.confirmation_count
        persisted_status = status
        resolved_at = now
        if status == "approved":
            next_count += 1
            if next_count < approval.required_confirmations:
                persisted_status = "pending"
                resolved_at = None

        statement = (
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.status == "pending",
                or_(ApprovalRequest.expires_at.is_(None), ApprovalRequest.expires_at > now),
            )
            .values(
                status=persisted_status,
                confirmation_count=next_count,
                resolved_at=resolved_at,
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
