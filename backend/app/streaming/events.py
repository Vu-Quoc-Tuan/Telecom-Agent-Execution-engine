# backend/app/streaming/events.py
from __future__ import annotations

from enum import StrEnum

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
    summary: str | None = None
    status: str


class TimelineUpdatedPayload(BaseStreamPayload):
    last_executed_node: str
    steps: list[TimelineStepItem]


class TextDeltaPayload(BaseStreamPayload):
    delta: str


class RunSuspendedPayload(BaseStreamPayload):
    approval_request_id: str
    reason: str


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
