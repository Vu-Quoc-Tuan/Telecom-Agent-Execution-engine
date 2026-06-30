from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.tool_calls import ToolCallRepository

_TOOL_ERROR_STATUSES = {"failed", "rejected", "cancelled", "timed_out"}


def _tool_output(tool_call) -> str | None:
    if tool_call is None:
        return None
    result = tool_call.result_json if isinstance(tool_call.result_json, dict) else {}
    output = result["output"] if "output" in result else tool_call.error_message
    return str(output) if output is not None else None


def serialize_timeline_steps(db: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    tool_calls_by_step = {
        tool_call.run_step_id: tool_call
        for tool_call in ToolCallRepository.get_tool_calls_by_run(db, run_id)
    }
    steps = RunStepRepository.get_steps_by_run(db, run_id)

    serialized: list[dict[str, Any]] = []
    for position, step in enumerate(steps, start=1):
        tool_call = tool_calls_by_step.get(step.id)
        payload: dict[str, Any] = {
            "id": str(step.id),
            "step_index": step.step_index,
            "step_type": step.step_type,
            "name": step.name,
            "display_title": step.name,
            "display_order": position,
            "summary": step.summary,
            "status": step.status,
        }
        if tool_call is not None:
            payload.update(
                {
                    "tool_name": tool_call.skill_name,
                    "connector_name": tool_call.connector_name,
                    "risk_level": tool_call.risk_level,
                    "tool_status": tool_call.status,
                    "tool_input": tool_call.arguments_json or {},
                    "tool_output": _tool_output(tool_call),
                    "is_error": tool_call.status in _TOOL_ERROR_STATUSES,
                    "output_truncated": bool(tool_call.output_truncated),
                }
            )
        serialized.append(payload)

    return serialized
