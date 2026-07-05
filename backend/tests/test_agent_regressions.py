from __future__ import annotations

import types
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.agent.state import MAX_CHECKPOINT_MESSAGES, AgentState, append_messages
from app.common.enums import RunStatus
from app.llm.schemas import (
    FinishReason,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    MessageRole,
    NormalizedToolCall,
    StreamEventType,
)


class FakeDb:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def execute(self, statement, *args, **kwargs):
        return types.SimpleNamespace(rowcount=0, scalars=lambda: [])

    def scalars(self, statement):
        return types.SimpleNamespace(all=lambda: [])


class FailingAgentApp:
    async def astream(self, *args, **kwargs):
        yield {"call_llm_gateway": {"execution_error": "provider failed"}}

    async def get_state(self, *args, **kwargs):
        return types.SimpleNamespace(
            values={
                "execution_error": "provider failed",
                "latest_response": None,
                "current_step_index": 1,
            }
        )


class CompletingAgentApp:
    async def astream(self, *args, **kwargs):
        yield {"call_llm_gateway": {"latest_response": "done"}}

    async def get_state(self, *args, **kwargs):
        return types.SimpleNamespace(
            values={
                "execution_error": None,
                "latest_response": LLMResponse(
                    provider="openai",
                    model="gpt-4o",
                    content="done",
                    finish_reason=FinishReason.STOP,
                ),
                "current_step_index": 1,
            }
        )


class CapturingAgentApp:
    def __init__(self, latest_response: LLMResponse | None = None):
        self.initial_state = None
        self.config = None
        self.latest_response = latest_response or LLMResponse(
            provider="openai",
            model="gpt-4o",
            content="done",
            finish_reason=FinishReason.STOP,
        )

    async def astream(self, initial_state, *args, **kwargs):
        self.initial_state = initial_state
        self.config = kwargs.get("config")
        yield {"call_llm_gateway": {"latest_response": self.latest_response}}

    async def get_state(self, *args, **kwargs):
        return types.SimpleNamespace(
            values={
                "execution_error": None,
                "latest_response": self.latest_response,
                "messages": [
                    LLMMessage(role=MessageRole.USER, content="check node"),
                    LLMMessage(role=MessageRole.ASSISTANT, content="tool output summary"),
                    LLMMessage(role=MessageRole.ASSISTANT, content=self.latest_response.content),
                ],
                "current_step_index": 1,
            }
        )


class TwoPhaseResumeAgentApp:
    def __init__(self, provider_tool_call_id: str, final_response: LLMResponse):
        self.provider_tool_call_id = provider_tool_call_id
        self.final_response = final_response
        self.get_state_calls = 0

    async def get_state(self, *args, **kwargs):
        self.get_state_calls += 1
        if self.get_state_calls == 1:
            return types.SimpleNamespace(
                values={
                    "latest_response": LLMResponse(
                        provider="openai",
                        model="gpt-4o",
                        finish_reason=FinishReason.TOOL,
                        tool_calls=[
                            NormalizedToolCall(
                                id=self.provider_tool_call_id,
                                name="ping_node",
                                arguments={"node_name": "site-a", "count": 3},
                            )
                        ],
                    )
                }
            )
        return types.SimpleNamespace(
            values={
                "execution_error": None,
                "latest_response": self.final_response,
                "current_step_index": 3,
            }
        )

    async def astream(self, *args, **kwargs):
        yield {"call_llm_gateway": {"latest_response": self.final_response}}


class AgentLifecycleRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_graph_config_isolates_checkpoints_by_run(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        session_id = uuid.uuid4()
        run_id = uuid.uuid4()

        config = AgentExecutionService._graph_config(
            FakeDb(),
            types.SimpleNamespace(),
            session_id,
            run_id=run_id,
        )

        self.assertEqual(str(run_id), config["configurable"]["thread_id"])

    def test_session_history_excludes_tool_rows_and_sanitizes_user_content(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        history = [
            types.SimpleNamespace(role="user", content="password=super-secret"),
            types.SimpleNamespace(role="assistant", content="Tôi đã kiểm tra node."),
            types.SimpleNamespace(role="tool", content="orphaned tool payload"),
            types.SimpleNamespace(role="user", content="tiếp tục"),
        ]

        messages = AgentExecutionService._llm_messages_from_history(history)

        self.assertEqual(
            [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.USER],
            [message.role for message in messages],
        )
        self.assertNotIn("super-secret", messages[0].content)
        self.assertNotIn("orphaned tool payload", [message.content for message in messages])

    async def test_async_graph_state_uses_async_checkpointer_api(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        expected_state = types.SimpleNamespace(values={"latest_response": "done"})

        class AsyncStateOnlyAgentApp:
            async def aget_state(self, *args, **kwargs):
                return expected_state

            def get_state(self, *args, **kwargs):
                raise AssertionError("sync graph state API must not be used")

        state = await AgentExecutionService._get_graph_state(
            AsyncStateOnlyAgentApp(),
            config={"configurable": {"thread_id": "session-1"}},
        )

        self.assertIs(expected_state, state)

    async def test_graph_execution_error_marks_run_failed(self) -> None:
        from app.agent.prompts import TELECOM_AGENT_PROMPT_VERSION
        from app.services.agent_execution import AgentExecutionService

        session_id = uuid.uuid4()
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=session_id,
            provider="openai",
            model="gpt-4o",
        )
        message = types.SimpleNamespace(run_id=None)
        statuses: list[str] = []
        create_run_kwargs = {}

        def update_status(db, run_id, status, error_msg=None, commit=True):
            statuses.append(status)
            return run

        def create_run(**kwargs):
            create_run_kwargs.update(kwargs)
            return run

        with (
            patch(
                "app.services.agent_execution.SessionRepository.get_session_by_id",
                return_value=object(),
            ),
            patch(
                "app.services.agent_execution.MessageRepository.save_message", return_value=message
            ),
            patch("app.services.agent_execution.RunRepository.create_run", side_effect=create_run),
            patch(
                "app.services.agent_execution.RunRepository.increment_step_count", return_value=run
            ),
            patch(
                "app.services.agent_execution.RunRepository.update_run_status",
                side_effect=update_status,
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                return_value=[],
            ),
            patch(
                "app.services.agent_execution.RunStepRepository.create_error_step",
                create=True,
            ) as create_error_step,
            patch(
                "app.services.agent_execution.MessageRepository.mark_pending_interventions_undelivered",
                create=True,
            ) as mark_undelivered,
            patch.object(AgentExecutionService, "_agent_app", FailingAgentApp()),
            patch.object(AgentExecutionService, "_serialize_steps", return_value=[]),
            patch(
                "app.services.agent_execution.telemetry_tracker.get_active_prompt_version",
                return_value=TELECOM_AGENT_PROMPT_VERSION,
            ),
        ):
            events = [
                event
                async for event in AgentExecutionService.run_agent_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    session_id=session_id,
                    user_content="check node",
                )
            ]

        self.assertEqual("run_failed", events[-1][0])
        self.assertIn(RunStatus.FAILED.value, statuses)
        self.assertEqual(TELECOM_AGENT_PROMPT_VERSION, create_run_kwargs["prompt_version"])
        create_error_step.assert_called_once_with(
            db=unittest.mock.ANY,
            run_id=run.id,
            summary="provider failed",
            metadata={"source": "agent_graph"},
            commit=False,
        )
        mark_undelivered.assert_called_once_with(
            unittest.mock.ANY,
            run.id,
            reason="provider failed",
            commit=False,
        )

    async def test_run_config_is_passed_to_initial_graph_state_and_config(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        session_id = uuid.uuid4()
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=session_id,
            provider="openai",
            model="gpt-4o",
            run_config_json={"temperature": 0.2, "max_steps": 7, "max_tokens": 321},
        )
        message = types.SimpleNamespace(run_id=None)
        agent_app = CapturingAgentApp()

        with (
            patch(
                "app.services.agent_execution.SessionRepository.get_session_by_id",
                return_value=object(),
            ),
            patch(
                "app.services.agent_execution.MessageRepository.save_message", return_value=message
            ),
            patch("app.services.agent_execution.RunRepository.create_run", return_value=run),
            patch(
                "app.services.agent_execution.RunRepository.increment_step_count", return_value=run
            ),
            patch(
                "app.services.agent_execution.RunRepository.update_run_status",
                return_value=types.SimpleNamespace(status=RunStatus.COMPLETED.value),
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                return_value=[],
            ),
            patch(
                "app.services.agent_execution.MessageRepository.requeue_undelivered_interventions",
                create=True,
            ) as requeue_interventions,
            patch.object(AgentExecutionService, "_agent_app", agent_app),
            patch.object(AgentExecutionService, "_serialize_steps", return_value=[]),
            patch("app.services.agent_execution.telemetry_tracker.trace_run_end"),
        ):
            events = [
                event
                async for event in AgentExecutionService.run_agent_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    session_id=session_id,
                    user_content="check node",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
        self.assertEqual(7, agent_app.initial_state["max_steps"])
        self.assertIsNone(agent_app.initial_state["execution_error"])
        self.assertIsNone(agent_app.initial_state["latest_response"])
        self.assertEqual(0.2, agent_app.config["configurable"]["run_config"]["temperature"])
        self.assertEqual(7, agent_app.config["configurable"]["run_config"]["max_steps"])
        self.assertEqual(321, agent_app.config["configurable"]["run_config"]["max_tokens"])
        requeue_interventions.assert_called_once_with(
            unittest.mock.ANY,
            session_id=session_id,
            run_id=run.id,
        )

    async def test_run_completion_closes_langfuse_trace(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        session_id = uuid.uuid4()
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=session_id,
            provider="openai",
            model="gpt-4o",
            run_config_json={},
        )
        message = types.SimpleNamespace(run_id=None)
        agent_app = CapturingAgentApp()

        with (
            patch(
                "app.services.agent_execution.SessionRepository.get_session_by_id",
                return_value=types.SimpleNamespace(title="Existing title"),
            ),
            patch(
                "app.services.agent_execution.MessageRepository.save_message", return_value=message
            ),
            patch("app.services.agent_execution.RunRepository.create_run", return_value=run),
            patch(
                "app.services.agent_execution.RunRepository.increment_step_count", return_value=run
            ),
            patch(
                "app.services.agent_execution.RunRepository.update_run_status",
                return_value=types.SimpleNamespace(status=RunStatus.COMPLETED.value),
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                return_value=[],
            ),
            patch.object(AgentExecutionService, "_agent_app", agent_app),
            patch.object(AgentExecutionService, "_serialize_steps", return_value=[]),
            patch(
                "app.services.agent_execution.telemetry_tracker.trace_run_end"
            ) as trace_run_end_mock,
        ):
            events = [
                event
                async for event in AgentExecutionService.run_agent_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    session_id=session_id,
                    user_content="check node",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
        # Trace cha của lượt chạy phải được đóng đúng 1 lần với trạng thái completed.
        trace_run_end_mock.assert_called_once()
        self.assertEqual("completed", trace_run_end_mock.call_args.kwargs["status"])

    async def test_title_generation_uses_sanitized_user_content(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        session_id = uuid.uuid4()
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=session_id,
            provider="openai",
            model="gpt-4o",
            run_config_json={},
        )
        session = types.SimpleNamespace(id=session_id, title="New Session")
        message = types.SimpleNamespace(run_id=None)

        class CapturingGateway:
            providers = ("openai",)

            def __init__(self):
                self.title_prompt = None

            async def invoke(self, provider=None, messages=None, options=None):
                self.title_prompt = messages[0].content
                return types.SimpleNamespace(content="Kiểm tra server")

        gateway = CapturingGateway()

        with (
            patch(
                "app.services.agent_execution.SessionRepository.get_session_by_id",
                return_value=session,
            ),
            patch(
                "app.services.agent_execution.MessageRepository.save_message", return_value=message
            ),
            patch("app.services.agent_execution.RunRepository.create_run", return_value=run),
            patch(
                "app.services.agent_execution.RunRepository.increment_step_count", return_value=run
            ),
            patch(
                "app.services.agent_execution.RunRepository.update_run_status",
                return_value=types.SimpleNamespace(status=RunStatus.COMPLETED.value),
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                return_value=[],
            ),
            patch.object(AgentExecutionService, "_agent_app", CompletingAgentApp()),
            patch.object(AgentExecutionService, "_serialize_steps", return_value=[]),
            patch("app.services.agent_execution.telemetry_tracker.trace_run_end"),
        ):
            stream = AgentExecutionService.run_agent_lifecycle(
                db=FakeDb(),
                llm_gateway=gateway,
                session_id=session_id,
                user_content="server tôi lag password=SSH_Master_Password_2026",
            )
            while True:
                event = await anext(stream)
                if event[0] == "run_completed":
                    break

            self.assertIsNone(gateway.title_prompt)
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)

        self.assertIsNotNone(gateway.title_prompt)
        self.assertNotIn("SSH_Master_Password_2026", gateway.title_prompt)
        self.assertIn("[[MASKED_SECRET]]", gateway.title_prompt)

    async def test_cancelled_run_is_not_reported_completed(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        session_id = uuid.uuid4()
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=session_id,
            provider="openai",
            model="gpt-4o",
            status=RunStatus.RUNNING.value,
        )
        message = types.SimpleNamespace(run_id=None)
        saved_roles: list[str] = []

        def save_message(db, session_id, run_id, role, content, metadata=None):
            saved_roles.append(role)
            return message

        def update_status(db, run_id, status, error_msg=None):
            if status == RunStatus.COMPLETED.value:
                run.status = RunStatus.CANCELLED.value
            else:
                run.status = status
            return run

        with (
            patch(
                "app.services.agent_execution.SessionRepository.get_session_by_id",
                return_value=object(),
            ),
            patch(
                "app.services.agent_execution.MessageRepository.save_message",
                side_effect=save_message,
            ),
            patch("app.services.agent_execution.RunRepository.create_run", return_value=run),
            patch(
                "app.services.agent_execution.RunRepository.increment_step_count", return_value=run
            ),
            patch(
                "app.services.agent_execution.RunRepository.update_run_status",
                side_effect=update_status,
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                return_value=[],
            ),
            patch.object(AgentExecutionService, "_agent_app", CompletingAgentApp()),
            patch.object(AgentExecutionService, "_serialize_steps", return_value=[]),
        ):
            events = [
                event
                async for event in AgentExecutionService.run_agent_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    session_id=session_id,
                    user_content="check node",
                )
            ]

        self.assertEqual(["user"], saved_roles)
        self.assertEqual("run_failed", events[-1][0])
        self.assertIn("cancelled", events[-1][1]["error"])

    async def test_unexpected_approved_tool_error_closes_run(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            provider="openai",
            model="gpt-4o",
            status=RunStatus.WAITING_APPROVAL.value,
        )
        tool_call = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_step_id=uuid.uuid4(),
            skill_name="ping_node",
            arguments_json={"node_name": "site-a", "count": 3},
        )
        approval = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            tool_call_id=tool_call.id,
            status="pending",
        )

        with (
            patch(
                "app.services.agent_execution.ApprovalRepository.get_request", return_value=approval
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.resolve_request",
                return_value=approval,
            ),
            patch("app.services.agent_execution.RunRepository.get_run", return_value=run),
            patch(
                "app.services.agent_execution.ToolCallRepository.get_tool_call",
                return_value=tool_call,
            ),
            patch("app.services.agent_execution.ToolCallRepository.start_execution"),
            patch("app.services.agent_execution.ToolCallRepository.save_result") as save_result,
            patch("app.services.agent_execution.RunStepRepository.complete_step") as complete_step,
            patch(
                "app.services.agent_execution.execute_builtin_tool",
                side_effect=RuntimeError("connector crashed"),
            ),
            patch.object(
                AgentExecutionService,
                "_mark_run_failed",
                return_value="connector crashed",
            ) as mark_failed,
            patch("app.services.agent_execution.telemetry_tracker.trace_run_end") as trace_run_end,
        ):
            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=approval.id,
                    action="approved",
                )
            ]

        self.assertEqual("run_failed", events[-1][0])
        self.assertEqual("failed", save_result.call_args.kwargs["status"])
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        mark_failed.assert_called_once()
        trace_run_end.assert_called_once_with(
            run_id=run.id.hex,
            output_content="connector crashed",
            status="failed",
        )

    async def test_rejected_approval_saves_tool_message_and_resume_telemetry(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        provider_tool_call_id = "call-danger"
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            provider="openai",
            model="gpt-4o",
            status=RunStatus.WAITING_APPROVAL.value,
            run_config_json={"temperature": 0.1, "max_steps": 10},
        )
        tool_call = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_step_id=uuid.uuid4(),
            skill_name="ping_node",
            arguments_json={"node_name": "site-a", "count": 3},
            provider_tool_call_id=provider_tool_call_id,
            result_json={"output": "Rejected by human operator. Reason: risky"},
            status="rejected",
        )
        approval = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            tool_call_id=tool_call.id,
            status="pending",
        )
        saved_roles: list[str] = []
        saved_contents: list[str] = []
        final_response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            content="ack",
            finish_reason=FinishReason.STOP,
        )
        agent_app = TwoPhaseResumeAgentApp(provider_tool_call_id, final_response)

        def save_message(db, session_id, run_id, role, content, metadata=None):
            saved_roles.append(role)
            saved_contents.append(content)
            return types.SimpleNamespace(id=uuid.uuid4())

        with (
            patch(
                "app.services.agent_execution.ApprovalRepository.get_request", return_value=approval
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.resolve_request",
                return_value=types.SimpleNamespace(**{**approval.__dict__, "status": "rejected"}),
            ),
            patch("app.services.agent_execution.RunRepository.get_run", return_value=run),
            patch(
                "app.services.agent_execution.ToolCallRepository.get_tool_call",
                return_value=tool_call,
            ),
            patch("app.services.agent_execution.ToolCallRepository.save_result"),
            patch("app.services.agent_execution.RunStepRepository.complete_step"),
            patch("app.services.agent_execution.MessageRepository.save_message"),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                return_value=[],
            ),
            patch(
                "app.services.agent_execution.ApprovalRepository.get_requests_by_run",
                return_value=[types.SimpleNamespace(status="rejected")],
            ),
            patch(
                "app.services.agent_execution.ToolCallRepository.get_tool_calls_by_run",
                return_value=[tool_call],
            ),
            patch(
                "app.services.agent_execution.RunRepository.update_run_status",
                return_value=types.SimpleNamespace(status=RunStatus.COMPLETED.value),
            ),
            patch(
                "app.services.agent_execution.RunRepository.increment_step_count", return_value=run
            ),
            patch(
                "app.services.agent_execution.MessageRepository.save_message",
                side_effect=save_message,
            ),
            patch.object(AgentExecutionService, "_agent_app", agent_app),
            patch.object(AgentExecutionService, "_serialize_steps", return_value=[]),
            patch(
                "app.services.agent_execution.telemetry_tracker.trace_run_end"
            ) as trace_run_end_mock,
        ):
            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=approval.id,
                    action="rejected",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
        self.assertIn("tool", saved_roles)
        self.assertTrue(any("HUMAN_REJECTED" in content for content in saved_contents))
        trace_run_end_mock.assert_called_once()


class ApprovalNodeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_cannot_call_tools_again_immediately_after_rejection(self) -> None:
        from app.agent.nodes import AgentNodes
        from app.llm.schemas import ToolChoiceMode

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            messages=[
                LLMMessage(
                    role=MessageRole.TOOL,
                    content='{"status":"rejected","code":"HUMAN_REJECTED"}',
                    tool_call_id="call-rejected",
                    tool_is_error=True,
                )
            ],
            approval_rejected=True,
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeGateway:
            providers = {"openai": object()}

            def __init__(self):
                self.options = None

            async def invoke(self, **kwargs):
                self.options = kwargs["options"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o",
                    content="Hành động đã bị từ chối và không được thực hiện.",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()
        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
        ):
            await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {},
                    }
                },
            )

        self.assertEqual(ToolChoiceMode.NONE, gateway.options.tool_choice.mode)

    async def test_call_llm_gateway_uses_run_config_options(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="check node")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeGateway:
            providers = {"openai": object()}

            def __init__(self):
                self.options = None

            async def invoke(self, **kwargs):
                self.options = kwargs["options"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o-mini",
                    content="done",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "run_config": {"temperature": 0.2, "max_tokens": 55},
                    }
                },
            )

        self.assertEqual("done", result["latest_response"].content)
        self.assertEqual("gpt-4o-mini", gateway.options.model)
        self.assertEqual(0.2, gateway.options.temperature)
        self.assertEqual(55, gateway.options.max_tokens)

    async def test_call_llm_gateway_normalizes_provider_case_and_model_override(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="check node")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeGateway:
            providers = {"openai": object()}

            def __init__(self):
                self.provider = None
                self.options = None

            async def invoke(self, **kwargs):
                self.provider = kwargs["provider"]
                self.options = kwargs["options"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o-mini",
                    content="done",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "OpenAI",
                        "model": "gpt-4o-mini",
                        "run_config": {"temperature": 0.2, "max_tokens": 55},
                    }
                },
            )

        self.assertEqual("done", result["latest_response"].content)
        self.assertEqual("openai", gateway.provider)
        self.assertEqual("gpt-4o-mini", gateway.options.model)

    async def test_call_llm_gateway_routes_to_other_configured_provider_on_failure(self) -> None:
        from app.agent.nodes import AgentNodes

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            messages=[LLMMessage(role=MessageRole.USER, content="check node")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeAdapter:
            def __init__(self, model):
                self.model = model

        class FakeGateway:
            providers = ("openai", "anthropic")

            def __init__(self):
                self.kwargs = None

            @staticmethod
            def get_adapter(provider):
                return FakeAdapter("gpt-4o" if provider == "openai" else "claude-sonnet")

            async def invoke(self, **kwargs):
                self.kwargs = kwargs
                return LLMResponse(
                    provider="anthropic",
                    model="claude-sonnet",
                    content="fallback worked",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()
        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "run_config": {},
                    }
                },
            )

        self.assertEqual("fallback worked", result["latest_response"].content)
        self.assertEqual(["anthropic"], gateway.kwargs["fallback_providers"])
        self.assertTrue(gateway.kwargs["fallback_on_non_retryable"])
        self.assertEqual(
            "claude-sonnet",
            gateway.kwargs["provider_options"]["anthropic"].model,
        )

    async def test_specific_skill_filters_catalog_and_forces_load_before_reasoning(self) -> None:
        from app.agent.nodes import AgentNodes
        from app.llm.schemas import ToolChoiceMode

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            messages=[LLMMessage(role=MessageRole.USER, content="check KPI")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        skills = [
            types.SimpleNamespace(name="check-kpis", description="Check KPI workflow."),
            types.SimpleNamespace(name="restart-site", description="Restart workflow."),
        ]

        class FakeGateway:
            providers = ("openai",)

            def __init__(self):
                self.options = None
                self.system_prompt = None
                self.tools = None

            async def invoke(self, **kwargs):
                self.options = kwargs["options"]
                self.system_prompt = kwargs["system_prompt"]
                self.tools = kwargs["tools"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o",
                    content="loading selected skill",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()
        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=skills),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
        ):
            await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {"selected_skill": "check-kpis"},
                    }
                },
            )

        self.assertEqual(ToolChoiceMode.SPECIFIC, gateway.options.tool_choice.mode)
        self.assertEqual("load_skill", gateway.options.tool_choice.tool_name)
        self.assertFalse(gateway.options.parallel_tool_calls)
        self.assertIn("check-kpis", gateway.system_prompt)
        self.assertNotIn("restart-site", gateway.system_prompt)
        load_skill = next(tool for tool in gateway.tools if tool.name == "load_skill")
        self.assertEqual(
            ["check-kpis"],
            load_skill.input_schema["properties"]["skill_name"]["enum"],
        )

    async def test_specific_skill_fails_if_it_is_no_longer_ready(self) -> None:
        from app.agent.nodes import AgentNodes

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            messages=[LLMMessage(role=MessageRole.USER, content="check KPI")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeGateway:
            providers = ("openai",)

            async def invoke(self, **kwargs):
                raise AssertionError("LLM must not run with a revoked selected skill")

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step") as complete_step,
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": FakeGateway(),
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {"selected_skill": "check-kpis"},
                    }
                },
            )

        self.assertIn("check-kpis", result["execution_error"])
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])

    async def test_call_llm_gateway_ignores_invalid_run_config_values(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="check node")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeGateway:
            providers = {"openai": object()}

            def __init__(self):
                self.options = None

            async def invoke(self, **kwargs):
                self.options = kwargs["options"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o",
                    content="done",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
            patch("app.agent.nodes.build_context_compaction_prompt") as build_compaction_prompt,
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {
                            "temperature": "hot",
                            "max_tokens": "many",
                            "timeout_seconds": "slow",
                            "context_window_tokens": "large",
                            "context_compaction_trigger_ratio": "soon",
                            "context_compaction_target_ratio": "small",
                        },
                    }
                },
            )

        self.assertEqual("done", result["latest_response"].content)
        self.assertIsNone(gateway.options.temperature)
        self.assertIsNone(gateway.options.max_tokens)
        self.assertIsNone(gateway.options.timeout_seconds)
        build_compaction_prompt.assert_not_called()

    async def test_call_llm_gateway_injects_pending_operator_interventions(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        intervention_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="check alarm")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        pending_intervention = types.SimpleNamespace(
            id=intervention_id,
            content="đừng hỏi nữa, tự chọn alarm phù hợp",
        )

        class CapturingGateway:
            providers = {"openai": object()}

            def __init__(self):
                self.messages = None

            async def invoke(self, **kwargs):
                self.messages = kwargs["messages"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o",
                    content="done",
                    finish_reason=FinishReason.STOP,
                )

        gateway = CapturingGateway()

        class TrackingDb(FakeDb):
            def __init__(self):
                self.commit_count = 0

            def commit(self):
                self.commit_count += 1

        db = TrackingDb()

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step") as complete_step,
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch(
                "app.agent.nodes.MessageRepository.list_pending_interventions",
                return_value=[pending_intervention],
            ),
            patch("app.agent.nodes.MessageRepository.mark_interventions_injected") as mark_injected,
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": db,
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {},
                        "settings": types.SimpleNamespace(),
                    }
                },
            )

        self.assertEqual("done", result["latest_response"].content)
        self.assertTrue(
            any(
                message.role is MessageRole.USER
                and "[OPERATOR INTERVENTION]" in (message.content or "")
                and "tự chọn alarm" in (message.content or "")
                for message in gateway.messages
            )
        )
        self.assertTrue(
            any(
                message.role is MessageRole.USER
                and "[OPERATOR INTERVENTION]" in (message.content or "")
                for message in result["messages"]
            )
        )
        complete_step.assert_called_once_with(
            db=db,
            step_id=step.id,
            status="completed",
            summary="done",
            metadata=unittest.mock.ANY,
            commit=False,
        )
        mark_injected.assert_called_once_with(db, [intervention_id], commit=False)
        self.assertEqual(1, db.commit_count)

    async def test_failed_llm_turn_rolls_back_without_consuming_interventions(self) -> None:
        from app.agent.nodes import AgentNodes

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            messages=[LLMMessage(role=MessageRole.USER, content="check alarm")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        intervention = types.SimpleNamespace(id=uuid.uuid4(), content="use table y")

        class FailingGateway:
            providers = ("openai",)

            async def invoke(self, **kwargs):
                raise RuntimeError("provider failed")

        class TrackingDb(FakeDb):
            def __init__(self):
                self.rollback_count = 0

            def rollback(self):
                self.rollback_count += 1

        db = TrackingDb()
        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch(
                "app.agent.nodes.MessageRepository.list_pending_interventions",
                return_value=[intervention],
            ),
            patch("app.agent.nodes.MessageRepository.mark_interventions_injected") as mark_injected,
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": db,
                        "llm_gateway": FailingGateway(),
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {},
                        "settings": types.SimpleNamespace(),
                    }
                },
            )

        self.assertEqual("provider failed", result["execution_error"])
        self.assertEqual(1, db.rollback_count)
        mark_injected.assert_not_called()

    def test_reliability_router_reenters_llm_when_late_intervention_is_pending(self) -> None:
        from app.agent.routing import reliability_router

        run_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="check alarm")],
            latest_response=LLMResponse(
                provider="openai",
                model="gpt-4o",
                content="final answer",
                finish_reason=FinishReason.STOP,
            ),
        )

        with patch(
            "app.agent.routing.MessageRepository.list_pending_interventions",
            return_value=[types.SimpleNamespace(id=uuid.uuid4())],
        ) as list_pending:
            route = reliability_router(state, {"configurable": {"db": FakeDb()}})

        self.assertEqual("call_llm_gateway", route)
        list_pending.assert_called_once()

    def test_reliability_router_ends_when_no_tools_or_pending_interventions(self) -> None:
        from app.agent.routing import reliability_router

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            messages=[LLMMessage(role=MessageRole.USER, content="check alarm")],
            latest_response=LLMResponse(
                provider="openai",
                model="gpt-4o",
                content="final answer",
                finish_reason=FinishReason.STOP,
            ),
        )

        with patch(
            "app.agent.routing.MessageRepository.list_pending_interventions",
            return_value=[],
        ):
            route = reliability_router(state, {"configurable": {"db": FakeDb()}})

        self.assertEqual("end", route)

    def test_reliability_router_accepts_final_answer_on_last_allowed_step(self) -> None:
        from app.agent.routing import reliability_router

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            current_step_index=10,
            max_steps=10,
            messages=[LLMMessage(role=MessageRole.USER, content="check alarm")],
            latest_response=LLMResponse(
                provider="openai",
                model="gpt-4o",
                content="final answer",
                finish_reason=FinishReason.STOP,
            ),
        )

        with patch(
            "app.agent.routing.MessageRepository.list_pending_interventions",
            return_value=[],
        ):
            route = reliability_router(state, {"configurable": {"db": FakeDb()}})

        self.assertEqual("end", route)

    def test_reliability_router_returns_invalid_known_tool_call_to_llm_feedback_path(self) -> None:
        from app.agent.routing import reliability_router

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            current_step_index=1,
            latest_response=LLMResponse(
                provider="openai",
                model="mistral-large",
                finish_reason=FinishReason.TOOL,
                tool_calls=[
                    NormalizedToolCall(
                        id="call-chained",
                        name="unknown_raw_tool",
                        arguments={"command": "hostname && uptime"},
                    )
                ],
            ),
        )

        route = reliability_router(state, {"configurable": {"db": FakeDb()}})

        self.assertEqual("fail", route)

    def test_reliability_router_routes_invalid_approval_tool_to_feedback_path(self) -> None:
        from app.agent.routing import reliability_router

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            current_step_index=1,
            latest_response=LLMResponse(
                provider="openai",
                model="gpt-4o",
                finish_reason=FinishReason.TOOL,
                tool_calls=[
                    NormalizedToolCall(
                        id="call-invalid-restart",
                        name="restart_service",
                        arguments={"node_name": "site-a", "service_name": "apache"},
                    )
                ],
            ),
        )
        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_NODE_HOST_MAP="",
            SSH_HOST="",
            SSH_RESTART_ALLOWED_SERVICES="nginx",
            SANDBOX_ENABLED=False,
        )

        route = reliability_router(state, {"configurable": {"db": FakeDb(), "settings": settings}})

        self.assertEqual("execute_tools", route)

    def test_reliability_router_keeps_mixed_invalid_and_approval_batch_on_feedback_path(
        self,
    ) -> None:
        from app.agent.routing import reliability_router

        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            current_step_index=1,
            latest_response=LLMResponse(
                provider="openai",
                model="mistral-large",
                finish_reason=FinishReason.TOOL,
                tool_calls=[
                    NormalizedToolCall(
                        id="call-invalid",
                        name="unknown_raw_tool",
                        arguments={"command": "hostname && uptime"},
                    ),
                    NormalizedToolCall(
                        id="call-approval",
                        name="removed_approval_tool",
                        arguments={"command": "systemctl restart sshd"},
                    ),
                ],
            ),
        )

        route = reliability_router(state, {"configurable": {"db": FakeDb()}})

        self.assertEqual("fail", route)

    async def test_restart_service_request_creates_single_approval(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            finish_reason=FinishReason.TOOL,
            tool_calls=[
                NormalizedToolCall(
                    id="call-restart",
                    name="restart_service",
                    arguments={"node_name": "site-a", "service_name": "nginx"},
                )
            ],
        )
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=response,
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        tool_call = types.SimpleNamespace(id=uuid.uuid4())
        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_NODE_HOST_MAP="site-a=10.0.0.11",
            SSH_HOST="",
            SSH_RESTART_ALLOWED_SERVICES="nginx",
        )

        with (
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.ToolCallRepository.get_by_idempotency_key", return_value=None),
            patch("app.agent.nodes.ToolCallRepository.create_tool_call", return_value=tool_call),
            patch("app.agent.nodes.ApprovalRepository.create_request") as create_request,
            patch("app.agent.nodes.execute_builtin_tool", new=AsyncMock()) as run_tool,
            patch("app.agent.nodes.interrupt", return_value={"messages": []}),
        ):
            result = await AgentNodes.suspend_for_human(
                state,
                {"configurable": {"db": FakeDb(), "settings": settings}},
            )

        self.assertEqual(2, result["current_step_index"])
        create_request.assert_called_once()
        self.assertNotIn("required_confirmations", create_request.call_args.kwargs)
        run_tool.assert_not_awaited()

    async def test_call_llm_gateway_includes_dynamic_resource_context_from_settings(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="server tôi bị lag check cho tôi")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        class FakeGateway:
            providers = {"openai": object()}

            def __init__(self):
                self.system_prompt = None

            async def invoke(self, **kwargs):
                self.system_prompt = kwargs["system_prompt"]
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o",
                    content="done",
                    finish_reason=FinishReason.STOP,
                )

        gateway = FakeGateway()
        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_HOST="",
            SSH_NODE_HOST_MAP="site-a=10.0.0.11",
            CLICKHOUSE_HOST="clickhouse.internal",
            EXTERNAL_POSTGRES_HOST="postgres.internal",
        )

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": gateway,
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {},
                        "settings": settings,
                    }
                },
            )

        self.assertEqual("done", result["latest_response"].content)
        self.assertIn("site-a", gateway.system_prompt)
        self.assertIn("SSH: Khả dụng", gateway.system_prompt)
        self.assertIn("ClickHouse: Khả dụng", gateway.system_prompt)
        self.assertIn("External PostgreSQL: Khả dụng", gateway.system_prompt)
        self.assertIn("Sandbox Python", gateway.system_prompt)

    async def test_call_llm_gateway_streams_text_delta_custom_events(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            messages=[LLMMessage(role=MessageRole.USER, content="chào")],
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        final_response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            content="Xin chào",
            finish_reason=FinishReason.STOP,
        )
        custom_events: list[dict] = []

        class StreamingGateway:
            providers = {"openai": object()}

            async def stream(self, **kwargs):
                yield LLMStreamChunk(
                    event_type=StreamEventType.TEXT_DELTA,
                    provider="openai",
                    model="gpt-4o",
                    content_delta="Xin ",
                )
                yield LLMStreamChunk(
                    event_type=StreamEventType.TEXT_DELTA,
                    provider="openai",
                    model="gpt-4o",
                    content_delta="chào",
                )
                yield LLMStreamChunk(
                    event_type=StreamEventType.FINISH,
                    provider="openai",
                    model="gpt-4o",
                    finish_reason=FinishReason.STOP,
                    is_final=True,
                    final_response=final_response,
                )

            async def invoke(self, **kwargs):
                raise AssertionError("invoke should not be used when stream returns final response")

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.SkillRepository.list_ready_skills", return_value=[]),
            patch("app.agent.nodes.MessageRepository.list_pending_interventions", return_value=[]),
            patch("app.agent.nodes.get_stream_writer", return_value=custom_events.append),
        ):
            result = await AgentNodes.call_llm_gateway(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "llm_gateway": StreamingGateway(),
                        "provider": "openai",
                        "model": "gpt-4o",
                        "run_config": {},
                        "settings": types.SimpleNamespace(),
                    }
                },
            )

        self.assertEqual("Xin chào", result["latest_response"].content)
        self.assertEqual(
            [
                {"event_type": "text_delta", "delta": "Xin "},
                {"event_type": "text_delta", "delta": "chào"},
            ],
            custom_events,
        )

    async def test_execute_tools_rejects_removed_raw_tool_if_router_is_bypassed(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        session_id = uuid.uuid4()
        response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            finish_reason=FinishReason.TOOL,
            tool_calls=[
                NormalizedToolCall(
                    id="call-danger",
                    name="run_ssh_command",
                    arguments={"node_name": "site-a", "command": "touch /tmp/pwn"},
                )
            ],
        )
        state = AgentState(
            session_id=str(session_id),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=response,
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step") as complete_step,
            patch("app.agent.nodes.MessageRepository.save_message"),
            patch(
                "app.agent.nodes.execute_builtin_tool", new=AsyncMock(return_value=("ran", False))
            ) as run_tool,
        ):
            result = await AgentNodes.execute_tools(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "settings": types.SimpleNamespace(CLICKHOUSE_HOST="clickhouse.test"),
                    }
                },
            )

        self.assertNotIn("execution_error", result)
        self.assertIn("TOOL_VALIDATION_ERROR", result["messages"][0].content)
        self.assertIn("not available", result["messages"][0].content)
        complete_step.assert_called_once()
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        run_tool.assert_not_awaited()

    async def test_execute_tools_rejects_invalid_tool_arguments_before_runtime(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        session_id = uuid.uuid4()
        response = LLMResponse(
            provider="openai",
            model="mimo-router-model",
            finish_reason=FinishReason.TOOL,
            tool_calls=[
                NormalizedToolCall(
                    id="call-invalid",
                    name="get_active_alarms",
                    arguments={},
                )
            ],
        )
        state = AgentState(
            session_id=str(session_id),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=response,
        )
        step = types.SimpleNamespace(id=uuid.uuid4())

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step") as complete_step,
            patch("app.agent.nodes.MessageRepository.save_message"),
            patch(
                "app.agent.nodes.execute_builtin_tool", new=AsyncMock(return_value=("ran", False))
            ) as run_tool,
        ):
            result = await AgentNodes.execute_tools(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "settings": types.SimpleNamespace(CLICKHOUSE_HOST="clickhouse.test"),
                    }
                },
            )

        self.assertNotIn("execution_error", result)
        self.assertEqual(1, len(result["messages"]))
        self.assertTrue(result["messages"][0].tool_is_error)
        self.assertIn("TOOL_VALIDATION_ERROR", result["messages"][0].content)
        self.assertIn("Invalid tool arguments", result["messages"][0].content)
        complete_step.assert_called_once()
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        run_tool.assert_not_awaited()

    async def test_suspend_for_human_rejects_invalid_tool_arguments_before_approval(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        session_id = uuid.uuid4()
        response = LLMResponse(
            provider="openai",
            model="mimo-router-model",
            finish_reason=FinishReason.TOOL,
            tool_calls=[
                NormalizedToolCall(
                    id="call-invalid-danger",
                    name="get_active_alarms",
                    arguments={"window_minutes": 30},
                )
            ],
        )
        state = AgentState(
            session_id=str(session_id),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=response,
        )
        error_step = types.SimpleNamespace(id=uuid.uuid4())

        with (
            patch("app.agent.nodes.ToolCallRepository.get_by_idempotency_key", return_value=None),
            patch(
                "app.agent.nodes.RunStepRepository.create_error_step", return_value=error_step
            ) as create_error_step,
            patch("app.agent.nodes.ApprovalRepository.create_request") as create_approval,
            patch(
                "app.agent.nodes.execute_builtin_tool", new=AsyncMock(return_value=("ran", False))
            ) as run_tool,
        ):
            result = await AgentNodes.suspend_for_human(
                state,
                {
                    "configurable": {
                        "db": FakeDb(),
                        "settings": types.SimpleNamespace(CLICKHOUSE_HOST="clickhouse.test"),
                    }
                },
            )

        self.assertIn("Invalid tool arguments", result["execution_error"])
        create_error_step.assert_called_once()
        create_approval.assert_not_called()
        run_tool.assert_not_awaited()

    def test_mark_undelivered_preserves_message_and_records_failure(self) -> None:
        from app.database.repositories.messages import MessageRepository

        message = types.SimpleNamespace(
            metadata_json={"kind": "operator_intervention", "intervention_status": "pending"}
        )

        class TrackingDb:
            def __init__(self):
                self.flush_count = 0
                self.commit_count = 0

            def flush(self):
                self.flush_count += 1

            def commit(self):
                self.commit_count += 1

        db = TrackingDb()
        with patch.object(
            MessageRepository,
            "list_pending_interventions",
            return_value=[message],
        ):
            count = MessageRepository.mark_pending_interventions_undelivered(
                db,
                uuid.uuid4(),
                reason="provider failed",
                commit=False,
            )

        self.assertEqual(1, count)
        self.assertEqual("undelivered", message.metadata_json["intervention_status"])
        self.assertEqual("provider failed", message.metadata_json["delivery_error"])
        self.assertEqual(1, db.flush_count)
        self.assertEqual(0, db.commit_count)

    def test_requeue_undelivered_intervention_tracks_original_run(self) -> None:
        from app.database.repositories.messages import MessageRepository

        old_run_id = uuid.uuid4()
        new_run_id = uuid.uuid4()
        message = types.SimpleNamespace(
            run_id=old_run_id,
            metadata_json={
                "kind": "operator_intervention",
                "intervention_status": "undelivered",
            },
        )

        class TrackingDb:
            def __init__(self):
                self.commit_count = 0

            def commit(self):
                self.commit_count += 1

        db = TrackingDb()
        with patch.object(
            MessageRepository,
            "list_undelivered_interventions",
            return_value=[message],
            create=True,
        ):
            count = MessageRepository.requeue_undelivered_interventions(
                db,
                session_id=uuid.uuid4(),
                run_id=new_run_id,
            )

        self.assertEqual(1, count)
        self.assertEqual(new_run_id, message.run_id)
        self.assertEqual("pending", message.metadata_json["intervention_status"])
        self.assertEqual(str(old_run_id), message.metadata_json["requeued_from_run_id"])
        self.assertEqual(1, db.commit_count)


class AgentStateRegressionTests(unittest.TestCase):
    def test_message_reducer_keeps_bounded_checkpoint_window(self) -> None:
        current = [
            LLMMessage(role=MessageRole.USER, content=f"old-{index}")
            for index in range(MAX_CHECKPOINT_MESSAGES)
        ]
        new_messages = [
            LLMMessage(role=MessageRole.ASSISTANT, content="new-1"),
            LLMMessage(role=MessageRole.USER, content="new-2"),
        ]

        reduced = append_messages(current, new_messages)

        self.assertEqual(MAX_CHECKPOINT_MESSAGES, len(reduced))
        self.assertEqual("old-2", reduced[0].content)
        self.assertEqual("new-2", reduced[-1].content)


class FakeApprovalDb:
    def __init__(self, approval):
        self.approval = approval

    def get(self, model, approval_id):
        return self.approval

    def commit(self) -> None:
        pass

    def refresh(self, approval) -> None:
        pass


class ApprovalExpiryRegressionTests(unittest.TestCase):
    def test_expired_approval_cannot_be_resolved(self) -> None:
        from app.database.repositories.approvals import ApprovalRepository

        approval = types.SimpleNamespace(
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        result = ApprovalRepository.resolve_request(
            FakeApprovalDb(approval),
            uuid.uuid4(),
            status="approved",
        )

        self.assertIsNone(result)
        self.assertEqual("expired", approval.status)


if __name__ == "__main__":
    unittest.main()
