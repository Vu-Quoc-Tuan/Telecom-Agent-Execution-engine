from __future__ import annotations

import json
import unittest

from app.streaming.event_mapper import TelecomStreamEventMapper
from app.streaming.events import TelecomStreamEventType
from app.streaming.sse import format_sse_event


class StreamingContractTests(unittest.TestCase):
    def test_formats_validated_payload_as_rest_sse(self) -> None:
        envelope = TelecomStreamEventMapper.map_raw_payload_to_envelope(
            "run_started",
            {"run_id": "run-1", "session_id": "session-1", "status": "running"},
        )

        encoded = format_sse_event(envelope.event_type.value, envelope.payload.model_dump())

        self.assertTrue(encoded.startswith("event: run_started\ndata: "))
        self.assertTrue(encoded.endswith("\n\n"))
        payload = json.loads(encoded.split("data: ", maxsplit=1)[1])
        self.assertEqual("run-1", payload["run_id"])

    def test_unknown_service_event_becomes_typed_error_event(self) -> None:
        envelope = TelecomStreamEventMapper.map_raw_payload_to_envelope(
            "unexpected",
            {"run_id": "run-1", "message": "unexpected event"},
        )

        self.assertEqual(TelecomStreamEventType.ERROR, envelope.event_type)
        self.assertEqual("unexpected event", envelope.payload.message)

    def test_text_delta_event_is_validated_and_formatted(self) -> None:
        envelope = TelecomStreamEventMapper.map_raw_payload_to_envelope(
            "text_delta",
            {"run_id": "run-1", "delta": "Xin chào"},
        )

        encoded = format_sse_event(envelope.event_type.value, envelope.payload.model_dump())

        self.assertEqual(TelecomStreamEventType.TEXT_DELTA, envelope.event_type)
        self.assertTrue(encoded.startswith("event: text_delta\ndata: "))
        payload = json.loads(encoded.split("data: ", maxsplit=1)[1])
        self.assertEqual({"run_id": "run-1", "delta": "Xin chào"}, payload)


if __name__ == "__main__":
    unittest.main()
