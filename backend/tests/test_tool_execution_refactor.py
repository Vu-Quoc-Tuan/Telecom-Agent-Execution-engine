from __future__ import annotations

import types
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.state import AgentState
from app.common.enums import RunStatus
from app.llm.schemas import FinishReason, LLMResponse, NormalizedToolCall


class FakeDb:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def execute(self, statement, *args, **kwargs):
        return types.SimpleNamespace(rowcount=0, scalars=lambda: [])

    def add(self, instance) -> None:
        pass

    def scalar(self, statement):
        return None

    def scalars(self, statement):
        return types.SimpleNamespace(all=lambda: [])


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


class ToolExecutionRefactorTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_tools_records_unexpected_runtime_error_as_failed_tool_message(
        self,
    ) -> None:
        from app.agent.nodes import AgentNodes
        from app.agent.tool_batch_planner import ToolBatchPlan, ToolPlanItem

        run_id = uuid.uuid4()
        session_id = uuid.uuid4()
        tool_call = NormalizedToolCall(
            id="call-crash",
            name="ping_node",
            arguments={"node_name": "site-a", "count": 3},
        )
        state = AgentState(
            session_id=str(session_id),
            run_id=str(run_id),
            current_step_index=1,
            latest_response=LLMResponse(
                provider="openai",
                model="gpt-4o",
                finish_reason=FinishReason.TOOL,
                tool_calls=[tool_call],
            ),
            tool_batch_plan=ToolBatchPlan(
                route="execute_tools",
                items=[ToolPlanItem(index=0, tool_call=tool_call, risk_level="auto_execute")],
            ),
        )
        step = types.SimpleNamespace(id=uuid.uuid4())
        persisted_tool_call = types.SimpleNamespace(id=uuid.uuid4())
        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_NODE_HOST_MAP="site-a=10.0.0.11",
            SSH_HOST="",
        )
        stream_writer = MagicMock()

        with ExitStack() as stack:
            build_plan = stack.enter_context(patch("app.agent.nodes.build_tool_batch_plan"))
            stack.enter_context(
                patch("app.agent.nodes.RunStepRepository.create_step", return_value=step)
            )
            stack.enter_context(patch("app.agent.nodes.RunStepRepository.start_step"))
            complete_step = stack.enter_context(
                patch("app.agent.nodes.RunStepRepository.complete_step")
            )
            stack.enter_context(
                patch(
                    "app.agent.nodes.ToolCallRepository.create_tool_call",
                    return_value=persisted_tool_call,
                )
            )
            stack.enter_context(patch("app.agent.nodes.ToolCallRepository.start_execution"))
            save_result = stack.enter_context(
                patch("app.agent.nodes.ToolCallRepository.save_result")
            )
            save_message = stack.enter_context(
                patch("app.agent.nodes.MessageRepository.save_message")
            )
            stack.enter_context(
                patch(
                    "app.agent.nodes.execute_builtin_tool",
                    new=AsyncMock(side_effect=RuntimeError("connector crashed")),
                )
            )
            stack.enter_context(patch("app.agent.nodes.telemetry_tracker.trace_span"))
            stack.enter_context(
                patch("app.agent.nodes.AgentNodes._custom_stream_writer", return_value=stream_writer)
            )

            result = await AgentNodes.execute_tools(
                state,
                {"configurable": {"db": FakeDb(), "settings": settings}},
            )

        self.assertEqual(2, result["current_step_index"])
        self.assertEqual(1, len(result["messages"]))
        self.assertEqual("connector crashed", result["messages"][0].content)
        self.assertTrue(result["messages"][0].tool_is_error)
        self.assertEqual("failed", save_result.call_args.kwargs["status"])
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        self.assertEqual("tool", save_message.call_args.kwargs["role"])
        stream_writer.assert_called_once_with(
            {
                "event_type": "timeline_updated",
                "last_executed_node": "execute_tools",
            }
        )
        build_plan.assert_not_called()

    def test_reliability_router_uses_cached_tool_batch_plan(self) -> None:
        from app.agent.routing import reliability_router
        from app.agent.tool_batch_planner import ToolBatchPlan, ToolPlanItem

        tool_call = NormalizedToolCall(
            id="call-needs-approval",
            name="restart_service",
            arguments={"node_name": "site-a", "service_name": "nginx"},
        )
        state = AgentState(
            session_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            current_step_index=1,
            latest_response=LLMResponse(
                provider="openai",
                model="gpt-4o",
                finish_reason=FinishReason.TOOL,
                tool_calls=[tool_call],
            ),
            tool_batch_plan=ToolBatchPlan(
                route="suspend_for_human",
                items=[ToolPlanItem(index=0, tool_call=tool_call, risk_level="require_approval")],
            ),
        )

        with patch("app.agent.routing.build_tool_batch_plan") as build_plan:
            route = reliability_router(state, {"configurable": {"db": FakeDb()}})

        self.assertEqual("suspend_for_human", route)
        build_plan.assert_not_called()

    async def test_approved_approval_tool_execution_records_tool_span(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        provider_tool_call_id = "call-approved"
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            provider="openai",
            model="gpt-4o",
            status=RunStatus.WAITING_APPROVAL.value,
            run_config_json={},
        )
        tool_call = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            run_step_id=uuid.uuid4(),
            skill_name="ping_node",
            arguments_json={"node_name": "site-a", "count": 3},
            risk_level="require_approval",
            provider_tool_call_id=provider_tool_call_id,
            result_json=None,
            status="waiting_approval",
        )
        approval_step_id = tool_call.run_step_id
        tool_step = types.SimpleNamespace(id=uuid.uuid4())
        approval = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            tool_call_id=tool_call.id,
            status="pending",
        )
        final_response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            content="ack",
            finish_reason=FinishReason.STOP,
        )
        agent_app = TwoPhaseResumeAgentApp(provider_tool_call_id, final_response)

        def save_result(**kwargs):
            tool_call.status = kwargs["status"]
            tool_call.result_json = kwargs["result"]
            return tool_call

        def attach_tool_call(db, tool_call_id, run_step_id):
            tool_call.run_step_id = run_step_id
            return tool_call

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_request",
                    return_value=approval,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.resolve_request",
                    return_value=types.SimpleNamespace(
                        **{**approval.__dict__, "status": "approved"}
                    ),
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.RunRepository.get_run", return_value=run)
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.get_tool_call",
                    return_value=tool_call,
                )
            )
            append_step = stack.enter_context(
                patch(
                    "app.services.agent_execution.RunStepRepository.append_step",
                    return_value=tool_step,
                )
            )
            stack.enter_context(patch("app.services.agent_execution.RunStepRepository.start_step"))
            attach_to_step = stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.attach_to_step",
                    side_effect=attach_tool_call,
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.ToolCallRepository.start_execution")
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.save_result",
                    side_effect=save_result,
                )
            )
            complete_step = stack.enter_context(
                patch("app.services.agent_execution.RunStepRepository.complete_step")
            )
            stack.enter_context(
                patch("app.services.agent_execution.MessageRepository.save_message")
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.execute_builtin_tool",
                    new=AsyncMock(return_value=("pong", False)),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_requests_by_run",
                    return_value=[types.SimpleNamespace(status="approved")],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.get_tool_calls_by_run",
                    return_value=[tool_call],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.RunRepository.update_run_status",
                    return_value=types.SimpleNamespace(status=RunStatus.COMPLETED.value),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.RunRepository.increment_step_count",
                    return_value=run,
                )
            )
            stack.enter_context(patch.object(AgentExecutionService, "_agent_app", agent_app))
            stack.enter_context(
                patch.object(AgentExecutionService, "_serialize_steps", return_value=[])
            )
            trace_span = stack.enter_context(
                patch("app.services.agent_execution.telemetry_tracker.trace_span")
            )
            stack.enter_context(
                patch("app.services.agent_execution.telemetry_tracker.trace_run_end")
            )

            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=approval.id,
                    action="approved",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
        self.assertEqual("timeline_updated", events[0][0])
        self.assertEqual("approved_tool_execution", events[0][1]["last_executed_node"])
        self.assertEqual(approval_step_id, complete_step.call_args_list[0].kwargs["step_id"])
        self.assertEqual("completed", complete_step.call_args_list[0].kwargs["status"])
        append_step.assert_called_once()
        self.assertEqual("tool_call", append_step.call_args.kwargs["step_type"])
        self.assertEqual("Skill Runtime: ping_node", append_step.call_args.kwargs["name"])
        attach_to_step.assert_called_once()
        self.assertEqual(tool_step.id, attach_to_step.call_args.args[2])
        trace_span.assert_called_once()
        self.assertEqual(run.id.hex, trace_span.call_args.kwargs["run_id"])
        self.assertEqual("completed", trace_span.call_args.kwargs["status"])

    async def test_unexpected_approved_tool_error_resumes_with_failed_tool_message(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        provider_tool_call_id = "call-approved-crash"
        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            provider="openai",
            model="gpt-4o",
            status=RunStatus.WAITING_APPROVAL.value,
            run_config_json={},
        )
        tool_call = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            run_step_id=uuid.uuid4(),
            skill_name="ping_node",
            arguments_json={"node_name": "site-a", "count": 3},
            risk_level="require_approval",
            provider_tool_call_id=provider_tool_call_id,
            result_json=None,
            status="waiting_approval",
        )
        approval_step_id = tool_call.run_step_id
        tool_step = types.SimpleNamespace(id=uuid.uuid4())
        approval = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            tool_call_id=tool_call.id,
            status="pending",
        )
        final_response = LLMResponse(
            provider="openai",
            model="gpt-4o",
            content="tool failed safely",
            finish_reason=FinishReason.STOP,
        )
        agent_app = TwoPhaseResumeAgentApp(provider_tool_call_id, final_response)
        saved_messages: list[tuple[str, str]] = []

        def save_result(**kwargs):
            tool_call.status = kwargs["status"]
            tool_call.result_json = kwargs["result"]
            return tool_call

        def attach_tool_call(db, tool_call_id, run_step_id):
            tool_call.run_step_id = run_step_id
            return tool_call

        def save_message(db, session_id, run_id, role, content, metadata=None):
            saved_messages.append((role, content))
            return types.SimpleNamespace(id=uuid.uuid4())

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_request",
                    return_value=approval,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.resolve_request",
                    return_value=types.SimpleNamespace(
                        **{**approval.__dict__, "status": "approved"}
                    ),
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.RunRepository.get_run", return_value=run)
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.get_tool_call",
                    return_value=tool_call,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.RunStepRepository.append_step",
                    return_value=tool_step,
                )
            )
            stack.enter_context(patch("app.services.agent_execution.RunStepRepository.start_step"))
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.attach_to_step",
                    side_effect=attach_tool_call,
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.ToolCallRepository.start_execution")
            )
            save_result_mock = stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.save_result",
                    side_effect=save_result,
                )
            )
            complete_step = stack.enter_context(
                patch("app.services.agent_execution.RunStepRepository.complete_step")
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.execute_builtin_tool",
                    new=AsyncMock(side_effect=RuntimeError("connector crashed")),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.MessageRepository.save_message",
                    side_effect=save_message,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_pending_requests",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_requests_by_run",
                    return_value=[types.SimpleNamespace(status="approved")],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.get_tool_calls_by_run",
                    return_value=[tool_call],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.RunRepository.update_run_status",
                    return_value=types.SimpleNamespace(status=RunStatus.COMPLETED.value),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.RunRepository.increment_step_count",
                    return_value=run,
                )
            )
            stack.enter_context(patch.object(AgentExecutionService, "_agent_app", agent_app))
            stack.enter_context(
                patch.object(AgentExecutionService, "_serialize_steps", return_value=[])
            )
            mark_failed = stack.enter_context(
                patch.object(AgentExecutionService, "_mark_run_failed")
            )
            stack.enter_context(
                patch("app.services.agent_execution.telemetry_tracker.trace_run_end")
            )

            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=approval.id,
                    action="approved",
                )
            ]

        self.assertEqual("run_completed", events[-1][0])
        self.assertEqual(approval_step_id, complete_step.call_args_list[0].kwargs["step_id"])
        self.assertEqual("completed", complete_step.call_args_list[0].kwargs["status"])
        self.assertEqual("failed", save_result_mock.call_args.kwargs["status"])
        self.assertEqual("failed", complete_step.call_args.kwargs["status"])
        self.assertIn(("tool", "connector crashed"), saved_messages)
        mark_failed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
