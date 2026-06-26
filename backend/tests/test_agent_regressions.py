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
                                name="run_ssh_command",
                                arguments={"node_name": "site-a", "command": "restart service"},
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

        def update_status(db, run_id, status, error_msg=None):
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
                "app.services.agent_execution.RunRepository.attach_langfuse_trace", return_value=run
            ),
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
            patch.object(AgentExecutionService, "_agent_app", FailingAgentApp()),
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

        self.assertEqual("run_failed", events[-1][0])
        self.assertIn(RunStatus.FAILED.value, statuses)
        self.assertEqual(TELECOM_AGENT_PROMPT_VERSION, create_run_kwargs["prompt_version"])
        create_error_step.assert_called_once_with(
            db=unittest.mock.ANY,
            run_id=run.id,
            summary="provider failed",
            metadata={"source": "agent_graph"},
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
                "app.services.agent_execution.RunRepository.attach_langfuse_trace", return_value=run
            ) as attach_trace,
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
            patch.object(AgentExecutionService, "_push_llm_telemetry"),
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
        self.assertEqual(run.id.hex, attach_trace.call_args.kwargs["trace_id"])

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
                "app.services.agent_execution.RunRepository.attach_langfuse_trace", return_value=run
            ),
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
            patch.object(AgentExecutionService, "_push_llm_telemetry"),
        ):
            events = [
                event
                async for event in AgentExecutionService.run_agent_lifecycle(
                    db=FakeDb(),
                    llm_gateway=gateway,
                    session_id=session_id,
                    user_content="server tôi lag password=SSH_Master_Password_2026",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
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
                "app.services.agent_execution.RunRepository.attach_langfuse_trace", return_value=run
            ),
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
            skill_name="run_ssh_command",
            arguments_json={"node_name": "site-a", "command": "restart service"},
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
        ):
            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=approval.id,
                    action="approved",
                    resolved_by="operator",
                )
            ]

        self.assertEqual("run_failed", events[-1][0])
        self.assertEqual("failed", save_result.call_args.kwargs["status"])
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        mark_failed.assert_called_once()

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
            skill_name="run_ssh_command",
            arguments_json={"node_name": "site-a", "command": "restart service"},
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
        final_response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            content="ack",
            finish_reason=FinishReason.STOP,
        )
        agent_app = TwoPhaseResumeAgentApp(provider_tool_call_id, final_response)

        def save_message(db, session_id, run_id, role, content, metadata=None):
            saved_roles.append(role)
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
            patch.object(AgentExecutionService, "_push_llm_telemetry") as push_telemetry,
        ):
            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=approval.id,
                    action="rejected",
                    resolved_by="operator",
                    note="risky",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
        self.assertIn("tool", saved_roles)
        push_telemetry.assert_called_once()


class ApprovalNodeRegressionTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIn("server tôi", gateway.system_prompt)
        self.assertIn("query_clickhouse", gateway.system_prompt)
        self.assertIn("query_postgres", gateway.system_prompt)
        self.assertIn("MỘT lệnh đơn", gateway.system_prompt)
        self.assertIn("&&", gateway.system_prompt)

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

    async def test_execute_tools_refuses_dangerous_action_if_router_is_bypassed(self) -> None:
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
            patch(
                "app.agent.nodes.execute_builtin_tool", new=AsyncMock(return_value=("ran", False))
            ) as run_tool,
        ):
            result = await AgentNodes.execute_tools(
                state,
                {"configurable": {"db": FakeDb()}},
            )

        self.assertIn("requires human approval", result["execution_error"])
        complete_step.assert_called_once()
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        run_tool.assert_not_awaited()

    async def test_suspension_advances_step_index(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            finish_reason=FinishReason.TOOL,
            tool_calls=[
                NormalizedToolCall(
                    id="call-1",
                    name="run_ssh_command",
                    arguments={"node_name": "site-a", "command": "systemctl restart x"},
                )
            ],
        )
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=response,
        )
        skill = types.SimpleNamespace(
            name="run_ssh_command",
            status="ready",
            connector_name="ssh",
            risk_level="dangerous_action",
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        tool_call = types.SimpleNamespace(id=uuid.uuid4())

        with (
            patch("app.agent.nodes.SkillRepository.get_skill_by_name", return_value=skill),
            patch("app.agent.nodes.RunStepRepository.create_step", return_value=step),
            patch("app.agent.nodes.ToolCallRepository.get_by_idempotency_key", return_value=None),
            patch("app.agent.nodes.ToolCallRepository.create_tool_call", return_value=tool_call),
            patch("app.agent.nodes.ApprovalRepository.create_request") as create_request,
            patch("app.agent.nodes.interrupt", return_value={"messages": []}),
        ):
            result = await AgentNodes.suspend_for_human(
                state,
                {"configurable": {"db": FakeDb()}},
            )

        self.assertEqual(2, result["current_step_index"])
        create_request.assert_called_once()

    async def test_mixed_tool_batch_only_requests_approval_for_dangerous_tools(self) -> None:
        from app.agent.nodes import AgentNodes

        run_id = uuid.uuid4()
        session_id = uuid.uuid4()
        response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            finish_reason=FinishReason.TOOL,
            tool_calls=[
                NormalizedToolCall(
                    id="call-read",
                    name="query_clickhouse",
                    arguments={"sql": "SELECT 1"},
                ),
                NormalizedToolCall(
                    id="call-danger",
                    name="run_ssh_command",
                    arguments={"node_name": "site-a", "command": "systemctl restart x"},
                ),
            ],
        )
        state = AgentState(
            session_id=str(session_id),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=response,
        )
        steps = [types.SimpleNamespace(id=uuid.uuid4()), types.SimpleNamespace(id=uuid.uuid4())]
        tool_calls = [
            types.SimpleNamespace(id=uuid.uuid4()),
            types.SimpleNamespace(id=uuid.uuid4()),
        ]

        with (
            patch("app.agent.nodes.RunStepRepository.create_step", side_effect=steps),
            patch("app.agent.nodes.RunStepRepository.start_step"),
            patch("app.agent.nodes.RunStepRepository.complete_step"),
            patch("app.agent.nodes.ToolCallRepository.get_by_idempotency_key", return_value=None),
            patch("app.agent.nodes.ToolCallRepository.create_tool_call", side_effect=tool_calls),
            patch("app.agent.nodes.ToolCallRepository.start_execution"),
            patch("app.agent.nodes.ToolCallRepository.save_result"),
            patch("app.agent.nodes.MessageRepository.save_message"),
            patch(
                "app.agent.nodes.execute_builtin_tool", new=AsyncMock(return_value=("rows", False))
            ) as run_tool,
            patch("app.agent.nodes.ApprovalRepository.create_request") as create_request,
            patch("app.agent.nodes.interrupt", return_value={"messages": []}),
        ):
            result = await AgentNodes.suspend_for_human(
                state,
                {"configurable": {"db": FakeDb()}},
            )

        self.assertEqual(3, result["current_step_index"])
        create_request.assert_called_once()
        run_tool.assert_awaited_once()


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
            resolved_by="operator",
        )

        self.assertIsNone(result)
        self.assertEqual("expired", approval.status)


if __name__ == "__main__":
    unittest.main()
