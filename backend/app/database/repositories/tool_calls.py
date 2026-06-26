from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models.tool_calls import ToolCall

_OPEN_TOOL_CALL_STATUSES = {"pending", "waiting_approval", "running"}


class ToolCallRepository:
    @staticmethod
    def create_tool_call(
        db: Session,
        run_id: uuid.UUID,
        run_step_id: uuid.UUID,
        skill_name: str,
        skill_source: str,
        connector_name: str | None,
        arguments: dict[str, Any],
        risk_level: str,
        requires_approval: bool,
        provider_tool_call_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ToolCall:
        tool_call = ToolCall(
            id=uuid.uuid4(),
            run_id=run_id,
            run_step_id=run_step_id,
            provider_tool_call_id=provider_tool_call_id,
            skill_name=skill_name,
            skill_source=skill_source,
            connector_name=connector_name,
            arguments_json=arguments,
            risk_level=risk_level,
            requires_approval=requires_approval,
            idempotency_key=idempotency_key,
            status="waiting_approval" if requires_approval else "pending",
        )
        db.add(tool_call)
        db.commit()
        db.refresh(tool_call)
        return tool_call

    @staticmethod
    def close_open_tool_calls_by_run(
        db: Session,
        run_id: uuid.UUID,
        status: str,
        output: str,
        commit: bool = True,
    ) -> int:
        now = datetime.now(UTC)
        result = db.execute(
            update(ToolCall)
            .where(
                ToolCall.run_id == run_id,
                ToolCall.status.in_(_OPEN_TOOL_CALL_STATUSES),
            )
            .values(
                status=status,
                result_json={"output": output},
                error_message=output,
                completed_at=now,
                updated_at=now,
            )
        )
        if commit:
            db.commit()
        return result.rowcount or 0

    @staticmethod
    def get_tool_call(db: Session, tool_call_id: uuid.UUID) -> ToolCall | None:
        return db.get(ToolCall, tool_call_id)

    @staticmethod
    def get_tool_calls_by_run(db: Session, run_id: uuid.UUID) -> list[ToolCall]:
        statement = (
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at.asc())
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def get_by_provider_call_id(
        db: Session,
        run_id: uuid.UUID,
        provider_tool_call_id: str,
    ) -> ToolCall | None:
        statement = select(ToolCall).where(
            ToolCall.run_id == run_id,
            ToolCall.provider_tool_call_id == provider_tool_call_id,
        )
        return db.scalar(statement)

    @staticmethod
    def get_by_idempotency_key(db: Session, idempotency_key: str) -> ToolCall | None:
        statement = select(ToolCall).where(ToolCall.idempotency_key == idempotency_key)
        return db.scalar(statement)

    @staticmethod
    def start_execution(db: Session, tool_call_id: uuid.UUID) -> ToolCall | None:
        tool_call = db.get(ToolCall, tool_call_id)
        if tool_call is None:
            return None
        tool_call.status = "running"
        tool_call.started_at = datetime.now(UTC)
        db.commit()
        db.refresh(tool_call)
        return tool_call

    @staticmethod
    def save_result(
        db: Session,
        tool_call_id: uuid.UUID,
        status: str,
        result: dict[str, Any] | None,
        latency_ms: int,
        error_msg: str | None = None,
        output_truncated: bool = False,
    ) -> ToolCall | None:
        tool_call = db.get(ToolCall, tool_call_id)
        if tool_call is None:
            return None
        tool_call.status = status
        tool_call.result_json = result
        tool_call.latency_ms = latency_ms
        tool_call.error_message = error_msg
        tool_call.output_truncated = output_truncated
        tool_call.completed_at = datetime.now(UTC)
        db.commit()
        db.refresh(tool_call)
        return tool_call
