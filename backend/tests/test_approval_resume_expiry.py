from __future__ import annotations

import types
import unittest
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.common.enums import RunStatus
from app.llm.schemas import FinishReason, LLMResponse, NormalizedToolCall


class FakeDb:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def add(self, instance) -> None:
        pass

    def scalar(self, statement):
        return None

    def scalars(self, statement):
        return types.SimpleNamespace(all=lambda: [])


class ApprovalResumeExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_expires_remaining_batch_approval_before_resuming(self) -> None:
        from app.services.agent_execution import AgentExecutionService

        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            provider="openai",
            model="gpt-4o",
            status=RunStatus.WAITING_APPROVAL.value,
            run_config_json={},
        )
        current_tool = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_step_id=uuid.uuid4(),
            skill_name="ping_node",
            arguments_json={"node_name": "site-a", "count": 3},
            provider_tool_call_id="call-approved",
            result_json={"output": "approved output"},
            status="completed",
        )
        expired_tool = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_step_id=uuid.uuid4(),
            skill_name="restart_service",
            arguments_json={"node_name": "site-a", "service_name": "nginx"},
            provider_tool_call_id="call-expired",
            result_json=None,
            status="waiting_approval",
        )
        current_approval = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            tool_call_id=current_tool.id,
            status="pending",
        )
        resolved_approval = types.SimpleNamespace(
            **{**current_approval.__dict__, "status": "approved"}
        )
        expired_approval = types.SimpleNamespace(
            id=uuid.uuid4(),
            run_id=run.id,
            tool_call_id=expired_tool.id,
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        class BatchResumeAgentApp:
            def __init__(self) -> None:
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
                                        id="call-approved",
                                        name="ping_node",
                                        arguments={"node_name": "site-a", "count": 3},
                                    ),
                                    NormalizedToolCall(
                                        id="call-expired",
                                        name="restart_service",
                                        arguments={
                                            "node_name": "site-a",
                                            "service_name": "nginx",
                                        },
                                    ),
                                ],
                            )
                        }
                    )
                return types.SimpleNamespace(
                    values={
                        "execution_error": None,
                        "latest_response": LLMResponse(
                            provider="openai",
                            model="gpt-4o",
                            content="should not resume",
                            finish_reason=FinishReason.STOP,
                        ),
                    }
                )

            async def astream(self, *args, **kwargs):
                yield {"call_llm_gateway": {"latest_response": "should not resume"}}

        def expire_pending_requests(db, run_id=None, **kwargs):
            if run_id == run.id:
                expired_approval.status = "expired"
                return 1
            return 0

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.get_request",
                    return_value=current_approval,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.resolve_request",
                    return_value=resolved_approval,
                )
            )
            expire_pending = stack.enter_context(
                patch(
                    "app.services.agent_execution.ApprovalRepository.expire_pending_requests",
                    side_effect=expire_pending_requests,
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.RunRepository.get_run", return_value=run)
            )
            stack.enter_context(
                patch("app.agent.tool_execution.RunRepository.get_run_fresh", return_value=run)
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.get_tool_call",
                    return_value=current_tool,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.start_execution",
                    side_effect=lambda db, tool_call_id: (
                        setattr(current_tool, "status", "running") or current_tool
                    ),
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.ToolCallRepository.save_result")
            )
            stack.enter_context(
                patch("app.services.agent_execution.RunStepRepository.complete_step")
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.RunStepRepository.append_step",
                    return_value=types.SimpleNamespace(id=uuid.uuid4()),
                )
            )
            stack.enter_context(patch("app.services.agent_execution.RunStepRepository.start_step"))
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.attach_to_step",
                    return_value=current_tool,
                )
            )
            stack.enter_context(
                patch("app.services.agent_execution.MessageRepository.save_message")
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.execute_builtin_tool",
                    new=AsyncMock(return_value=("approved output", False)),
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
                    return_value=[resolved_approval, expired_approval],
                )
            )
            stack.enter_context(
                patch(
                    "app.services.agent_execution.ToolCallRepository.get_tool_calls_by_run",
                    return_value=[current_tool, expired_tool],
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
            stack.enter_context(
                patch.object(AgentExecutionService, "_serialize_steps", return_value=[])
            )
            stack.enter_context(
                patch.object(AgentExecutionService, "_agent_app", BatchResumeAgentApp())
            )
            mark_failed = stack.enter_context(
                patch.object(
                    AgentExecutionService,
                    "_mark_run_failed_and_close_trace",
                    return_value="Approval batch expired or was cancelled before completion.",
                )
            )

            events = [
                event
                async for event in AgentExecutionService.resolve_approval_and_resume_lifecycle(
                    db=FakeDb(),
                    llm_gateway=types.SimpleNamespace(),
                    approval_id=current_approval.id,
                    action="approved",
                )
            ]

        expire_pending.assert_called_once()
        mark_failed.assert_called_once()
        self.assertEqual("run_failed", events[-1][0])
        self.assertIn("expired", events[-1][1]["error"])


if __name__ == "__main__":
    unittest.main()
