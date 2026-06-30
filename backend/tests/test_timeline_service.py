from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch


class TimelineSerializationTests(unittest.TestCase):
    def test_waiting_approval_tool_call_is_not_serialized_as_error(self) -> None:
        from app.services.timeline import serialize_timeline_steps

        run_id = uuid.uuid4()
        step_id = uuid.uuid4()
        step = SimpleNamespace(
            id=step_id,
            step_index=1,
            step_type="approval",
            name="Chờ phê duyệt: restart_service",
            summary="Tool requires operator review.",
            status="waiting_approval",
        )
        tool_call = SimpleNamespace(
            run_step_id=step_id,
            skill_name="restart_service",
            connector_name="ssh",
            risk_level="dangerous_action",
            status="waiting_approval",
            arguments_json={"service": "mme"},
            result_json=None,
            error_message=None,
            output_truncated=False,
        )

        with (
            patch("app.services.timeline.RunStepRepository.get_steps_by_run", return_value=[step]),
            patch(
                "app.services.timeline.ToolCallRepository.get_tool_calls_by_run",
                return_value=[tool_call],
            ),
        ):
            payload = serialize_timeline_steps(db=object(), run_id=run_id)

        self.assertEqual("waiting_approval", payload[0]["status"])
        self.assertEqual("waiting_approval", payload[0]["tool_status"])
        self.assertFalse(payload[0]["is_error"])

    def test_terminal_tool_failures_are_serialized_as_errors(self) -> None:
        from app.services.timeline import serialize_timeline_steps

        run_id = uuid.uuid4()
        step_id = uuid.uuid4()
        step = SimpleNamespace(
            id=step_id,
            step_index=1,
            step_type="tool_call",
            name="query_clickhouse",
            summary="Tool failed.",
            status="failed",
        )
        tool_call = SimpleNamespace(
            run_step_id=step_id,
            skill_name="query_clickhouse",
            connector_name="clickhouse",
            risk_level="read_only",
            status="failed",
            arguments_json={"query": "SELECT 1"},
            result_json=None,
            error_message="Connection refused.",
            output_truncated=False,
        )

        with (
            patch("app.services.timeline.RunStepRepository.get_steps_by_run", return_value=[step]),
            patch(
                "app.services.timeline.ToolCallRepository.get_tool_calls_by_run",
                return_value=[tool_call],
            ),
        ):
            payload = serialize_timeline_steps(db=object(), run_id=run_id)

        self.assertTrue(payload[0]["is_error"])

    def test_falsy_tool_outputs_are_preserved_when_output_key_exists(self) -> None:
        from app.services.timeline import serialize_timeline_steps

        run_id = uuid.uuid4()
        cases = [
            ("empty-output", ""),
            ("zero-output", 0),
        ]

        for name, output in cases:
            with self.subTest(name=name):
                step_id = uuid.uuid4()
                step = SimpleNamespace(
                    id=step_id,
                    step_index=1,
                    step_type="tool_call",
                    name=name,
                    summary="Tool completed.",
                    status="completed",
                )
                tool_call = SimpleNamespace(
                    run_step_id=step_id,
                    skill_name=name,
                    connector_name=None,
                    risk_level="read_only",
                    status="completed",
                    arguments_json={},
                    result_json={"output": output},
                    error_message=None,
                    output_truncated=False,
                )

                with (
                    patch(
                        "app.services.timeline.RunStepRepository.get_steps_by_run",
                        return_value=[step],
                    ),
                    patch(
                        "app.services.timeline.ToolCallRepository.get_tool_calls_by_run",
                        return_value=[tool_call],
                    ),
                ):
                    payload = serialize_timeline_steps(db=object(), run_id=run_id)

                self.assertEqual(str(output), payload[0]["tool_output"])


if __name__ == "__main__":
    unittest.main()
