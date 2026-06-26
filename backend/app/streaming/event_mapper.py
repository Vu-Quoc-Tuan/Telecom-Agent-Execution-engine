# backend/app/streaming/event_mapper.py
from __future__ import annotations

from typing import Any

from app.streaming.events import (
    ErrorPayload,
    RunCompletedPayload,
    RunFailedPayload,
    RunResumedPayload,
    RunStartedPayload,
    RunSuspendedPayload,
    StreamEventEnvelope,
    TelecomStreamEventType,
    TextDeltaPayload,
    TimelineUpdatedPayload,
)


class TelecomStreamEventMapper:
    @staticmethod
    def map_raw_payload_to_envelope(event_type: str, data: dict[str, Any]) -> StreamEventEnvelope:
        """
        Validate và biến đổi dữ liệu Dict thô thành Object Pydantic có cấu trúc.
        """
        try:
            matched_type = TelecomStreamEventType(event_type)
        except ValueError:
            matched_type = TelecomStreamEventType.ERROR

        if matched_type == TelecomStreamEventType.RUN_STARTED:
            payload_obj = RunStartedPayload(**data)
        elif matched_type == TelecomStreamEventType.TEXT_DELTA:
            payload_obj = TextDeltaPayload(**data)
        elif matched_type == TelecomStreamEventType.TIMELINE_UPDATED:
            payload_obj = TimelineUpdatedPayload(**data)
        elif matched_type == TelecomStreamEventType.RUN_SUSPENDED:
            payload_obj = RunSuspendedPayload(**data)
        elif matched_type == TelecomStreamEventType.RUN_RESUMED:
            payload_obj = RunResumedPayload(**data)
        elif matched_type == TelecomStreamEventType.RUN_COMPLETED:
            payload_obj = RunCompletedPayload(**data)
        elif matched_type == TelecomStreamEventType.RUN_FAILED:
            payload_obj = RunFailedPayload(**data)
        else:
            matched_type = TelecomStreamEventType.ERROR
            payload_obj = ErrorPayload(
                run_id=data.get("run_id"),
                message=data.get("message", "Unknown pipeline error"),
                error_code=data.get("error_code"),
            )

        return StreamEventEnvelope(event_type=matched_type, payload=payload_obj)
