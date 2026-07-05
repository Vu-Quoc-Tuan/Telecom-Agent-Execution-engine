import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
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
    def get_skill_telemetry(
        db: Session,
        skill_names: list[str],
        lookback_days: int = 30,
    ) -> dict[str, dict[str, object]]:
        if not skill_names:
            return {}

        skill_name_list = list(set(skill_names))
        package_loader_tools = {"load_skill", "read_skill_file"}
        terminal_error_statuses = {"failed", "rejected", "cancelled", "timed_out"}
        telemetry: dict[str, dict[str, object]] = {
            skill_name: {
                "call_count": 0,
                "average_latency_ms": None,
                "error_rate": 0.0,
                "error_count": 0,
                "last_called_at": None,
            }
            for skill_name in skill_names
        }

        argument_skill_name = ToolCall.arguments_json["skill_name"].as_string()
        matched_skill_name = case(
            (ToolCall.skill_name.in_(skill_name_list), ToolCall.skill_name),
            (ToolCall.skill_name.in_(package_loader_tools), argument_skill_name),
        ).label("matched_skill_name")
        error_count = func.sum(
            case((ToolCall.status.in_(terminal_error_statuses), 1), else_=0)
        ).label("error_count")
        since = datetime.now(UTC) - timedelta(days=max(1, lookback_days))
        statement = (
            select(
                matched_skill_name,
                func.count(ToolCall.id).label("call_count"),
                func.avg(ToolCall.latency_ms).label("average_latency_ms"),
                error_count,
                func.max(ToolCall.created_at).label("last_called_at"),
            )
            .where(
                ToolCall.created_at >= since,
                or_(
                    ToolCall.skill_name.in_(skill_name_list),
                    and_(
                        ToolCall.skill_name.in_(package_loader_tools),
                        argument_skill_name.in_(skill_name_list),
                    ),
                ),
            )
            .group_by(matched_skill_name)
        )
        for row in db.execute(statement).all():
            matched = row.matched_skill_name
            if matched not in telemetry:
                continue
            call_count = int(row.call_count or 0)
            row_error_count = int(row.error_count or 0)
            item = telemetry[matched]
            item["call_count"] = call_count
            item["error_count"] = row_error_count
            item["average_latency_ms"] = (
                round(float(row.average_latency_ms)) if row.average_latency_ms is not None else None
            )
            item["error_rate"] = row_error_count / call_count if call_count else 0.0
            if row.last_called_at is not None:
                item["last_called_at"] = row.last_called_at.isoformat()

        return telemetry

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
