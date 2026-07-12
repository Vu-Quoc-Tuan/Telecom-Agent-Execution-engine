from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.agent.builtin_tools import connector_name_for
from app.common.enums import RunStatus
from app.common.exceptions import TelecomAgentException
from app.database.repositories.messages import MessageRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.runs import RunRepository
from app.database.repositories.tool_calls import ToolCallRepository
from app.llm.schemas import LLMMessage, MessageRole
from app.observability.langfuse import telemetry_tracker

ToolExecutor = Callable[..., Awaitable[tuple[str, bool]]]

_SKIP_TOOL_STATUSES = frozenset(
    {
        RunStatus.CANCELLED.value,
        RunStatus.TIMED_OUT.value,
        RunStatus.FAILED.value,
        RunStatus.COMPLETED.value,
        "rejected",
    }
)


def _runtime_error_message(exc: Exception) -> str:
    if isinstance(exc, TelecomAgentException):
        return exc.message
    return str(exc) or type(exc).__name__


def _terminal_run_status(db, run_id: uuid.UUID) -> str | None:
    # Must use a fresh DB read: agent holds a long-lived Session with
    # expire_on_commit=False, while cancel/timeout commit on another session.
    run = RunRepository.get_run_fresh(db, run_id)
    if run is None:
        return None
    if RunRepository.is_terminal_status(run.status):
        return run.status
    return None


def _fresh_tool_call(tool_call_repository, db, tool_call: Any) -> Any:
    """Re-load tool call status from DB when concurrent cancel may have closed it."""
    tool_call_id = getattr(tool_call, "id", None)
    if tool_call_id is None:
        return tool_call
    get_fresh = getattr(tool_call_repository, "get_tool_call_fresh", None)
    if not callable(get_fresh):
        return tool_call
    fresh = get_fresh(db, tool_call_id)
    return fresh if fresh is not None else tool_call


def _skip_status_for_terminal_run(terminal_status: str) -> str:
    if terminal_status == RunStatus.TIMED_OUT.value:
        return RunStatus.TIMED_OUT.value
    if terminal_status == RunStatus.CANCELLED.value:
        return RunStatus.CANCELLED.value
    # completed/failed/etc. — still do not execute side effects
    return RunStatus.CANCELLED.value


def _skip_output_for_status(status: str) -> str:
    if status == RunStatus.TIMED_OUT.value:
        return "Run timed out before tool execution."
    if status == RunStatus.CANCELLED.value:
        return "Run was cancelled before tool execution."
    return f"Run is already terminal with status '{status}'."


def _persist_skipped_tool_execution(
    *,
    db,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    step_id: uuid.UUID,
    tool_name: str,
    provider_tool_call_id: str | None,
    db_tool_call: Any | None,
    status: str,
    output: str,
    tool_call_repository=ToolCallRepository,
    run_step_repository=RunStepRepository,
    message_repository=MessageRepository,
    message_metadata: dict[str, Any] | None = None,
) -> LLMMessage:
    """Close lifecycle records without calling the real executor."""
    if db_tool_call is not None and getattr(db_tool_call, "status", None) == "running":
        tool_call_repository.save_result(
            db=db,
            tool_call_id=db_tool_call.id,
            status=status,
            result={"output": output},
            latency_ms=0,
            error_msg=output,
            output_truncated=False,
        )

    run_step_repository.complete_step(
        db=db,
        step_id=step_id,
        status=status,
        summary=output,
    )

    metadata = {
        "tool_name": tool_name,
        "tool_call_id": provider_tool_call_id,
        "skipped_reason": status,
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
    tool_call_id = provider_tool_call_id
    if tool_call_id is None and db_tool_call is not None:
        tool_call_id = str(db_tool_call.id)
    return LLMMessage(
        role=MessageRole.TOOL,
        content=output,
        tool_call_id=tool_call_id or "",
        tool_is_error=True,
    )


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
    on_started: Callable[[], None] | None = None,
) -> LLMMessage:
    """Execute one tool and persist the same lifecycle records for graph and resume paths.

    Cancel/timeout only update DB status; the background agent task may still be alive.
    This function is the last gate before any real executor side effect: if the run is
    already terminal, or the tool call cannot enter ``running``, the executor is not called.
    """
    terminal_status = _terminal_run_status(db, run_id)
    if terminal_status is not None:
        skip_status = _skip_status_for_terminal_run(terminal_status)
        return _persist_skipped_tool_execution(
            db=db,
            run_id=run_id,
            session_id=session_id,
            step_id=step_id,
            tool_name=tool_name,
            provider_tool_call_id=provider_tool_call_id,
            db_tool_call=existing_tool_call,
            status=skip_status,
            output=_skip_output_for_status(skip_status),
            tool_call_repository=tool_call_repository,
            run_step_repository=run_step_repository,
            message_repository=message_repository,
            message_metadata=message_metadata,
        )

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
        # Approval path: cancel may have closed this row on another session.
        db_tool_call = _fresh_tool_call(tool_call_repository, db, existing_tool_call)

    current_status = getattr(db_tool_call, "status", None)
    if current_status in _SKIP_TOOL_STATUSES:
        # Never re-execute a tool call that already left the startable set.
        if current_status == "completed":
            existing_output = ""
            result_json = getattr(db_tool_call, "result_json", None) or {}
            if isinstance(result_json, dict):
                existing_output = str(result_json.get("output", ""))
            return LLMMessage(
                role=MessageRole.TOOL,
                content=existing_output,
                tool_call_id=provider_tool_call_id or str(db_tool_call.id),
                tool_is_error=False,
            )
        skip_status = (
            current_status
            if current_status
            in {RunStatus.CANCELLED.value, RunStatus.TIMED_OUT.value, "rejected"}
            else RunStatus.CANCELLED.value
        )
        return _persist_skipped_tool_execution(
            db=db,
            run_id=run_id,
            session_id=session_id,
            step_id=step_id,
            tool_name=tool_name,
            provider_tool_call_id=provider_tool_call_id,
            db_tool_call=db_tool_call,
            status=skip_status,
            output=_skip_output_for_status(skip_status),
            tool_call_repository=tool_call_repository,
            run_step_repository=run_step_repository,
            message_repository=message_repository,
            message_metadata=message_metadata,
        )

    started = tool_call_repository.start_execution(db, db_tool_call.id)
    if started is not None:
        db_tool_call = started
    # Concurrent cancel can close the row after start_execution's local view.
    db_tool_call = _fresh_tool_call(tool_call_repository, db, db_tool_call)

    if getattr(db_tool_call, "status", None) != "running":
        # Concurrent cancel/timeout closed the tool call; do not execute.
        skip_status = getattr(db_tool_call, "status", None)
        if skip_status not in {RunStatus.CANCELLED.value, RunStatus.TIMED_OUT.value}:
            skip_status = RunStatus.CANCELLED.value
        return _persist_skipped_tool_execution(
            db=db,
            run_id=run_id,
            session_id=session_id,
            step_id=step_id,
            tool_name=tool_name,
            provider_tool_call_id=provider_tool_call_id,
            db_tool_call=db_tool_call,
            status=skip_status,
            output=_skip_output_for_status(skip_status),
            tool_call_repository=tool_call_repository,
            run_step_repository=run_step_repository,
            message_repository=message_repository,
            message_metadata=message_metadata,
        )

    # Tool call is running, but cancel/timeout may have landed after create/start.
    terminal_status = _terminal_run_status(db, run_id)
    if terminal_status is not None:
        skip_status = _skip_status_for_terminal_run(terminal_status)
        return _persist_skipped_tool_execution(
            db=db,
            run_id=run_id,
            session_id=session_id,
            step_id=step_id,
            tool_name=tool_name,
            provider_tool_call_id=provider_tool_call_id,
            db_tool_call=db_tool_call,
            status=skip_status,
            output=_skip_output_for_status(skip_status),
            tool_call_repository=tool_call_repository,
            run_step_repository=run_step_repository,
            message_repository=message_repository,
            message_metadata=message_metadata,
        )

    if on_started is not None:
        on_started()
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
    """
    Lưu kết quả từ chối tool call.
    """
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
