from __future__ import annotations

import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.common.enums import RunStatus
from app.services.agent_execution import AgentExecutionService


class FakeRunRecord:
    def __init__(self, id=None, session_id=None, status="running"):
        self.id = id or uuid.uuid4()
        self.session_id = session_id or uuid.uuid4()
        self.status = status
        self.provider = "openai"
        self.model = "gpt-4o"
        self.error_msg = None


class FakeMessage:
    def __init__(self, id=None, run_id=None):
        self.id = id
        self.run_id = run_id


class FakeResult:
    @property
    def rowcount(self) -> int:
        return 0

    def scalars(self, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.all.return_value = []
        return mock_res


class FakeDb:
    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def execute(self, *args, **kwargs):
        return FakeResult()

    def scalar(self, *args, **kwargs):
        return 1

    def scalars(self, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.all.return_value = []
        return mock_res


class FakeLLMGateway:
    pass


class FakeAgentApp:
    def __init__(self, stream_chunks, final_state_values):
        self.stream_chunks = stream_chunks
        self.final_state_values = final_state_values

    async def astream(self, *args, **kwargs):
        for chunk in self.stream_chunks:
            yield chunk

    async def get_state(self, *args, **kwargs):
        state = MagicMock()
        state.values = self.final_state_values
        return state


class AgentExecutionServiceTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.agent_execution.AgentExecutionService._mark_run_failed")
    @patch("app.services.agent_execution.MessageRepository.save_message")
    async def test_empty_model_response_fails_instead_of_using_completion_fallback(
        self,
        mock_save_message,
        mock_mark_run_failed,
    ):
        fake_run = FakeRunRecord()
        fake_response = MagicMock()
        fake_response.content = ""
        fake_app = FakeAgentApp(
            stream_chunks=[],
            final_state_values={"latest_response": fake_response, "execution_error": None},
        )
        mock_mark_run_failed.return_value = "Model returned an empty response."

        event = await AgentExecutionService._finalize_graph_run(
            db=FakeDb(),
            agent_app=fake_app,
            graph_config={},
            run_record=fake_run,
            session_id=fake_run.session_id,
            trace_id=fake_run.id.hex,
            execution_error_source="agent_graph",
            completion_default_text="Tác vụ hoàn thành.",
            missing_response_error="Agent graph ended without a final response.",
            empty_content_uses_default=True,
        )

        self.assertEqual(
            ("run_failed", {"run_id": str(fake_run.id), "error": "Model returned an empty response."}),
            event,
        )
        mock_mark_run_failed.assert_called_once()
        mock_save_message.assert_not_called()

    @patch("app.services.agent_execution.SessionRepository.get_session_by_id")
    @patch("app.services.agent_execution.MessageRepository.save_message")
    @patch("app.services.agent_execution.RunRepository.create_run")
    @patch("app.services.agent_execution.RunRepository.increment_step_count")
    @patch("app.services.agent_execution.RunRepository.update_run_status")
    @patch("app.services.agent_execution.AgentExecutionService.get_agent_app")
    @patch("app.services.agent_execution.AgentExecutionService._serialize_steps")
    @patch("app.services.agent_execution.RunStepRepository.create_error_step")
    async def test_run_agent_lifecycle_success(
        self,
        mock_err_step,
        mock_serialize,
        mock_get_app,
        mock_update_run,
        mock_inc_step,
        mock_create_run,
        mock_save_msg,
        mock_get_session,
    ):
        mock_get_session.return_value = True
        fake_run = FakeRunRecord()
        mock_create_run.return_value = fake_run
        mock_save_msg.return_value = FakeMessage()
        mock_serialize.return_value = []

        fake_response = MagicMock()
        fake_response.content = "All tasks completed."

        # Simulate agent yielding one node update
        fake_app = FakeAgentApp(
            stream_chunks=[{"call_llm_gateway": {}}],
            final_state_values={"latest_response": fake_response, "execution_error": None},
        )
        mock_get_app.return_value = fake_app

        events = []
        async for evt, data in AgentExecutionService.run_agent_lifecycle(
            db=FakeDb(),
            llm_gateway=FakeLLMGateway(),
            session_id=fake_run.session_id,
            user_content="Test command",
        ):
            events.append((evt, data))

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0][0], "run_started")
        self.assertEqual(events[1][0], "timeline_updated")
        self.assertEqual(events[2][0], "run_completed")
        self.assertEqual(events[2][1]["final_answer"], "All tasks completed.")

    @patch("app.services.agent_execution.SessionRepository.get_session_by_id")
    @patch("app.services.agent_execution.MessageRepository.save_message")
    @patch("app.services.agent_execution.RunRepository.create_run")
    @patch("app.services.agent_execution.RunRepository.increment_step_count")
    @patch("app.services.agent_execution.RunRepository.update_run_status")
    @patch("app.services.agent_execution.AgentExecutionService.get_agent_app")
    @patch("app.services.agent_execution.AgentExecutionService._serialize_steps")
    @patch("app.services.agent_execution.RunStepRepository.create_error_step")
    async def test_run_agent_lifecycle_emits_text_delta_custom_events(
        self,
        mock_err_step,
        mock_serialize,
        mock_get_app,
        mock_update_run,
        mock_inc_step,
        mock_create_run,
        mock_save_msg,
        mock_get_session,
    ):
        mock_get_session.return_value = True
        fake_run = FakeRunRecord()
        mock_create_run.return_value = fake_run
        mock_save_msg.return_value = FakeMessage()
        mock_serialize.return_value = []

        fake_response = MagicMock()
        fake_response.content = "Xin chào"

        fake_app = FakeAgentApp(
            stream_chunks=[
                ("custom", {"event_type": "text_delta", "delta": "Xin "}),
                ("custom", {"event_type": "text_delta", "delta": "chào"}),
                ("updates", {"call_llm_gateway": {}}),
            ],
            final_state_values={"latest_response": fake_response, "execution_error": None},
        )
        mock_get_app.return_value = fake_app

        events = []
        async for evt, data in AgentExecutionService.run_agent_lifecycle(
            db=FakeDb(),
            llm_gateway=FakeLLMGateway(),
            session_id=fake_run.session_id,
            user_content="Test command",
        ):
            events.append((evt, data))

        self.assertEqual("run_started", events[0][0])
        self.assertEqual(("text_delta", {"run_id": str(fake_run.id), "delta": "Xin "}), events[1])
        self.assertEqual(("text_delta", {"run_id": str(fake_run.id), "delta": "chào"}), events[2])
        self.assertEqual("timeline_updated", events[3][0])
        self.assertEqual("run_completed", events[4][0])

    @patch("app.services.agent_execution.SessionRepository.get_session_by_id")
    @patch("app.services.agent_execution.MessageRepository.save_message")
    @patch("app.services.agent_execution.RunRepository.create_run")
    @patch("app.services.agent_execution.RunRepository.increment_step_count")
    @patch("app.services.agent_execution.RunRepository.update_run_status")
    @patch("app.services.agent_execution.AgentExecutionService.get_agent_app")
    @patch("app.services.agent_execution.AgentExecutionService._serialize_steps")
    @patch("app.services.agent_execution.RunStepRepository.create_error_step")
    async def test_run_agent_lifecycle_emits_running_tool_timeline_from_custom_event(
        self,
        mock_err_step,
        mock_serialize,
        mock_get_app,
        mock_update_run,
        mock_inc_step,
        mock_create_run,
        mock_save_msg,
        mock_get_session,
    ):
        mock_get_session.return_value = True
        fake_run = FakeRunRecord()
        mock_create_run.return_value = fake_run
        mock_save_msg.return_value = FakeMessage()
        running_steps = [
            {
                "id": "tool-step",
                "step_index": 1,
                "step_type": "tool_call",
                "name": "Skill Runtime: lookup",
                "status": "running",
                "tool_input": {"site": "HNI"},
                "tool_output": None,
            }
        ]
        mock_serialize.return_value = running_steps

        fake_response = MagicMock()
        fake_response.content = "Done"
        fake_app = FakeAgentApp(
            stream_chunks=[
                (
                    "custom",
                    {
                        "event_type": "timeline_updated",
                        "last_executed_node": "execute_tools",
                    },
                ),
                ("updates", {"execute_tools": {}}),
            ],
            final_state_values={"latest_response": fake_response, "execution_error": None},
        )
        mock_get_app.return_value = fake_app

        events = []
        async for event in AgentExecutionService.run_agent_lifecycle(
            db=FakeDb(),
            llm_gateway=FakeLLMGateway(),
            session_id=fake_run.session_id,
            user_content="Run lookup",
        ):
            events.append(event)

        timeline_events = [event for event in events if event[0] == "timeline_updated"]
        self.assertEqual(2, len(timeline_events))
        self.assertEqual(running_steps, timeline_events[0][1]["steps"])
        self.assertEqual("execute_tools", timeline_events[0][1]["last_executed_node"])

    @patch("app.services.agent_execution.MessageRepository.mark_pending_interventions_undelivered")
    @patch("app.services.agent_execution.SessionRepository.get_session_by_id")
    @patch("app.services.agent_execution.MessageRepository.save_message")
    @patch("app.services.agent_execution.RunRepository.create_run")
    @patch("app.services.agent_execution.RunRepository.update_run_status")
    @patch("app.services.agent_execution.AgentExecutionService.get_agent_app")
    @patch("app.services.agent_execution.RunStepRepository.create_error_step")
    async def test_run_agent_lifecycle_execution_error(
        self,
        mock_err_step,
        mock_get_app,
        mock_update_run,
        mock_create_run,
        mock_save_msg,
        mock_get_session,
        mock_mark_undelivered,
    ):
        mock_get_session.return_value = True
        fake_run = FakeRunRecord()
        mock_create_run.return_value = fake_run
        mock_save_msg.return_value = FakeMessage()

        # Simulate execution error in state
        fake_app = FakeAgentApp(
            stream_chunks=[],
            final_state_values={
                "latest_response": None,
                "execution_error": "Tool execution failed",
            },
        )
        mock_get_app.return_value = fake_app
        mock_update_run.return_value = FakeRunRecord(status=RunStatus.FAILED.value)

        events = []
        async for evt, data in AgentExecutionService.run_agent_lifecycle(
            db=FakeDb(),
            llm_gateway=FakeLLMGateway(),
            session_id=fake_run.session_id,
            user_content="Test error",
        ):
            events.append((evt, data))

        self.assertEqual(events[0][0], "run_started")
        self.assertEqual(events[-1][0], "run_failed")
        self.assertEqual(events[-1][1]["error"], "Tool execution failed")
        mock_mark_undelivered.assert_called_once_with(
            unittest.mock.ANY,
            fake_run.id,
            reason="Tool execution failed",
            commit=False,
        )


if __name__ == "__main__":
    unittest.main()
