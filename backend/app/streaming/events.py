# backend/app/streaming/events.py
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class TelecomStreamEventType(StrEnum):
    RUN_STARTED = "run_started"
    TEXT_DELTA = "text_delta"
    TIMELINE_UPDATED = "timeline_updated"
    RUN_SUSPENDED = "run_suspended"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    ERROR = "error"


class BaseStreamPayload(BaseModel):
    run_id: str


class RunStartedPayload(BaseStreamPayload):
    session_id: str
    status: str = "running"


class TimelineStepItem(BaseModel):
    id: str
    step_index: int
    step_type: str
    name: str
    display_title: str | None = None
    display_order: int | None = None
    summary: str | None = None
    status: str
    tool_name: str | None = None
    connector_name: str | None = None
    risk_level: str | None = None
    tool_status: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    is_error: bool | None = None
    output_truncated: bool | None = None


class TimelineUpdatedPayload(BaseStreamPayload):
    last_executed_node: str
    steps: list[TimelineStepItem]


class TextDeltaPayload(BaseStreamPayload):
    delta: str


class RunSuspendedPayload(BaseStreamPayload):
    approval_request_id: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    risk_level: str | None = None
    required_confirmations: int = 1
    confirmation_count: int = 0


class RunResumedPayload(BaseStreamPayload):
    action_taken: str


class RunCompletedPayload(BaseStreamPayload):
    final_answer: str


class RunFailedPayload(BaseStreamPayload):
    error: str


class ErrorPayload(BaseModel):
    run_id: str | None = None
    message: str
    error_code: str | None = None


class StreamEventEnvelope(BaseModel):
    """Vỏ bọc tối cao định dạng gói tin trước khi đẩy qua đường sse.py"""

    event_type: TelecomStreamEventType
    payload: BaseModel
