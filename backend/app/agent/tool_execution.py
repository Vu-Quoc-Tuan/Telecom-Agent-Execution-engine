from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.agent.builtin_tools import connector_name_for
from app.common.exceptions import TelecomAgentException
from app.database.repositories.messages import MessageRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.tool_calls import ToolCallRepository
from app.llm.schemas import LLMMessage, MessageRole
from app.observability.langfuse import telemetry_tracker

ToolExecutor = Callable[..., Awaitable[tuple[str, bool]]]


def _runtime_error_message(exc: Exception) -> str:
    if isinstance(exc, TelecomAgentException):
        return exc.message
    return str(exc) or type(exc).__name__


async def execute_and_persist_tool_call(
    *,
    db,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    step_id: uuid.UUID,
    tool_name: str,
    arguments: dict[str, Any],
    provider_tool_call_id: str | None,
    risk_level: str,
    settings: Any,
    executor: ToolExecutor,
    idempotency_key: str | None = None,
    step_index: int = 0,
    existing_tool_call: Any | None = None,
    approval_confirmations: int = 0,
    tool_call_repository=ToolCallRepository,
    run_step_repository=RunStepRepository,
    message_repository=MessageRepository,
    telemetry=telemetry_tracker,
    message_metadata: dict[str, Any] | None = None,
) -> LLMMessage:
    """Execute one tool and persist the same lifecycle records for graph and resume paths."""
    if existing_tool_call is None:
        db_tool_call = tool_call_repository.create_tool_call(
            db=db,
            run_id=run_id,
            run_step_id=step_id,
            skill_name=tool_name,
            connector_name=connector_name_for(tool_name),
            arguments=arguments,
            risk_level=risk_level,
            requires_approval=False,
            provider_tool_call_id=provider_tool_call_id,
            idempotency_key=idempotency_key,
        )
    else:
        db_tool_call = existing_tool_call

    tool_call_repository.start_execution(db, db_tool_call.id)
    started_at = datetime.now(UTC)
    try:
        executor_kwargs: dict[str, Any] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "db": db,
        }
        if settings is not None:
            executor_kwargs["settings"] = settings
        if approval_confirmations:
            executor_kwargs["approval_confirmations"] = approval_confirmations
        output, was_truncated = await executor(**executor_kwargs)
        status = "completed"
        error_message = None
    except Exception as exc:
        output = _runtime_error_message(exc)
        was_truncated = False
        status = "failed"
        error_message = output

    ended_at = datetime.now(UTC)
    latency_ms = int((ended_at - started_at).total_seconds() * 1000)
    tool_call_repository.save_result(
        db=db,
        tool_call_id=db_tool_call.id,
        status=status,
        result={"output": output},
        latency_ms=latency_ms,
        error_msg=error_message,
        output_truncated=was_truncated,
    )
    run_step_repository.complete_step(db=db, step_id=step_id, status=status, summary=output)

    try:
        turn_index = telemetry.get_turn_index(run_id.hex)
        telemetry.trace_span(
            run_id=run_id.hex,
            span_name=f"tool: {tool_name} #{turn_index}.{step_index}",
            input_data=arguments,
            output_data=output,
            start_time=started_at,
            end_time=ended_at,
            status=status,
        )
    except Exception:
        pass

    metadata = {
        "tool_name": tool_name,
        "tool_call_id": provider_tool_call_id,
        **(message_metadata or {}),
    }
    message_repository.save_message(
        db=db,
        session_id=session_id,
        run_id=run_id,
        role=MessageRole.TOOL.value,
        content=output,
        metadata=metadata,
    )
    return LLMMessage(
        role=MessageRole.TOOL,
        content=output,
        tool_call_id=provider_tool_call_id or str(db_tool_call.id),
        tool_is_error=status == "failed",
    )


def persist_rejected_tool_call(
    *,
    db,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    tool_call: Any,
    tool_call_repository=ToolCallRepository,
    run_step_repository=RunStepRepository,
    message_repository=MessageRepository,
) -> LLMMessage:
    output = json.dumps(
        {
            "status": "rejected",
            "code": "HUMAN_REJECTED",
            "message": "The human operator rejected this tool call. It was not executed.",
            "reason": "Rejected by operator.",
        },
        ensure_ascii=False,
    )
    run_step_repository.complete_step(
        db=db,
        step_id=tool_call.run_step_id,
        status="failed",
        summary=output,
    )
    tool_call_repository.save_result(
        db=db,
        tool_call_id=tool_call.id,
        status="rejected",
        result={"output": output},
        latency_ms=0,
        error_msg=output,
        output_truncated=False,
    )
    provider_tool_call_id = getattr(tool_call, "provider_tool_call_id", None)
    message_repository.save_message(
        db=db,
        session_id=session_id,
        run_id=run_id,
        role=MessageRole.TOOL.value,
        content=output,
        metadata={
            "tool_name": tool_call.skill_name,
            "tool_call_id": provider_tool_call_id,
            "approval_status": "rejected",
        },
    )
    return LLMMessage(
        role=MessageRole.TOOL,
        content=output,
        tool_call_id=provider_tool_call_id or str(tool_call.id),
        tool_is_error=True,
    )
