from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from app.agent.builtin_tools import (
    BUILTIN_TOOL_NAMES,
    build_builtin_tool_definitions,
    classify_builtin_risk,
    connector_name_for,
    execute_builtin_tool,
)
from app.agent.context_window import compact_messages_if_needed
from app.agent.prompts import build_system_prompt
from app.agent.state import AgentState
from app.common.enums import StepType
from app.common.exceptions import TelecomAgentException
from app.database.repositories.approvals import ApprovalRepository
from app.database.repositories.messages import MessageRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.skills import SkillRepository
from app.database.repositories.tool_calls import ToolCallRepository
from app.llm.gateway import LLMGateway
from app.llm.schemas import LLMMessage, LLMRequestOptions, MessageRole, StreamEventType


class AgentNodes:
    @staticmethod
    def _custom_stream_writer():
        try:
            return get_stream_writer()
        except RuntimeError:
            return None

    @staticmethod
    async def _invoke_llm_gateway_with_text_stream(
        *,
        llm_gateway: LLMGateway,
        messages: list[LLMMessage],
        system_prompt: str,
        llm_tools,
        provider: str | None,
        options: LLMRequestOptions | None,
    ):
        stream_method = getattr(llm_gateway, "stream", None)
        if not callable(stream_method):
            return await llm_gateway.invoke(
                messages=messages,
                system_prompt=system_prompt,
                tools=llm_tools,
                provider=provider,
                options=options,
            )

        writer = AgentNodes._custom_stream_writer()
        final_response = None
        async for stream_chunk in stream_method(
            messages=messages,
            system_prompt=system_prompt,
            tools=llm_tools,
            provider=provider,
            options=options,
        ):
            if (
                stream_chunk.event_type == StreamEventType.TEXT_DELTA
                and stream_chunk.content_delta
                and writer is not None
            ):
                writer(
                    {
                        "event_type": "text_delta",
                        "delta": stream_chunk.content_delta,
                    }
                )
            if stream_chunk.is_final and stream_chunk.final_response is not None:
                final_response = stream_chunk.final_response

        if final_response is not None:
            return final_response

        return await llm_gateway.invoke(
            messages=messages,
            system_prompt=system_prompt,
            tools=llm_tools,
            provider=provider,
            options=options,
        )

    @staticmethod
    async def suspend_for_human(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Persist an approval batch, pause the graph, then append resumed tool messages."""
        db = config["configurable"]["db"]
        run_uuid = uuid.UUID(state.run_id)
        session_uuid = uuid.UUID(state.session_id)
        tool_calls = state.latest_response.tool_calls if state.latest_response else []

        for index, tool_call in enumerate(tool_calls):
            idempotency_key = f"approval:{run_uuid}:{tool_call.id}"
            existing = ToolCallRepository.get_by_idempotency_key(db, idempotency_key)
            if existing is not None:
                continue

            risk_level = classify_builtin_risk(tool_call.name, tool_call.arguments)
            if risk_level == "dangerous_action":
                step = RunStepRepository.create_step(
                    db=db,
                    run_id=run_uuid,
                    step_index=state.current_step_index + index,
                    step_type=StepType.APPROVAL.value,
                    name=f"Chờ phê duyệt: {tool_call.name}",
                    summary="Tool batch contains a dangerous action and requires operator review.",
                    status="waiting_approval",
                )
                db_tool_call = ToolCallRepository.create_tool_call(
                    db=db,
                    run_id=run_uuid,
                    run_step_id=step.id,
                    skill_name=tool_call.name,
                    skill_source="internal",
                    connector_name=connector_name_for(tool_call.name),
                    arguments=tool_call.arguments,
                    risk_level=risk_level,
                    requires_approval=True,
                    provider_tool_call_id=tool_call.id,
                    idempotency_key=idempotency_key,
                )
                ApprovalRepository.create_request(
                    db=db,
                    run_id=run_uuid,
                    tool_call_id=db_tool_call.id,
                    reason=(
                        "Tool batch requires human review before execution. "
                        f"Tool: {tool_call.name}; risk: {risk_level}; "
                        f"arguments: {tool_call.arguments}"
                    ),
                    expires_in_seconds=1800,
                )
                continue

            step = RunStepRepository.create_step(
                db=db,
                run_id=run_uuid,
                step_index=state.current_step_index + index,
                step_type=StepType.TOOL_CALL.value,
                name=f"Skill Runtime: {tool_call.name}",
            )
            RunStepRepository.start_step(db, step.id)
            db_tool_call = ToolCallRepository.create_tool_call(
                db=db,
                run_id=run_uuid,
                run_step_id=step.id,
                skill_name=tool_call.name,
                skill_source="internal",
                connector_name=connector_name_for(tool_call.name),
                arguments=tool_call.arguments,
                risk_level=risk_level,
                requires_approval=False,
                provider_tool_call_id=tool_call.id,
                idempotency_key=idempotency_key,
            )
            ToolCallRepository.start_execution(db, db_tool_call.id)
            started_at = datetime.now(UTC)
            try:
                settings = config["configurable"].get("settings")
                if settings is not None:
                    output, was_truncated = await execute_builtin_tool(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        db=db,
                        settings=settings,
                    )
                else:
                    output, was_truncated = await execute_builtin_tool(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        db=db,
                    )
                status = "completed"
                error_message = None
            except TelecomAgentException as exc:
                output = exc.message
                was_truncated = False
                status = "failed"
                error_message = exc.message
            latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            ToolCallRepository.save_result(
                db=db,
                tool_call_id=db_tool_call.id,
                status=status,
                result={"output": output},
                latency_ms=latency_ms,
                error_msg=error_message,
                output_truncated=was_truncated,
            )
            RunStepRepository.complete_step(db=db, step_id=step.id, status=status, summary=output)
            MessageRepository.save_message(
                db=db,
                session_id=session_uuid,
                run_id=run_uuid,
                role="tool",
                content=output,
                metadata={"tool_name": tool_call.name, "tool_call_id": tool_call.id},
            )

        resume_payload = interrupt(
            {
                "run_id": state.run_id,
                "tool_call_ids": [tool_call.id for tool_call in tool_calls],
            }
        )
        return {
            "messages": [
                LLMMessage.model_validate(message) for message in resume_payload.get("messages", [])
            ],
            "current_step_index": state.current_step_index + len(tool_calls),
        }

    @staticmethod
    async def fail_unsafe_or_exhausted(state: AgentState) -> dict[str, Any]:
        if state.current_step_index >= state.max_steps:
            message = f"Agent exceeded the maximum of {state.max_steps} steps."
        else:
            message = "Agent requested a missing, unapproved, or prohibited skill."
        return {"execution_error": message}

    @staticmethod
    async def call_llm_gateway(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Call the provider-independent LLM gateway and persist an LLM timeline step."""
        db = config["configurable"]["db"]
        llm_gateway: LLMGateway = config["configurable"]["llm_gateway"]
        run_uuid = uuid.UUID(state.run_id)

        step = RunStepRepository.create_step(
            db=db,
            run_id=run_uuid,
            step_index=state.current_step_index,
            step_type=StepType.LLM_CALL.value,
            name="AI Core Reasoner",
        )
        RunStepRepository.start_step(db, step.id)

        # Catalog skill 'ready' chỉ chèn METADATA (name+description) vào system prompt
        # (progressive disclosure L1); bộ tool gửi cho LLM là các tool built-in cố định.
        ready_skills = SkillRepository.list_ready_skills(db)
        settings = config["configurable"].get("settings")
        system_prompt = build_system_prompt(ready_skills, settings=settings)
        llm_tools = build_builtin_tool_definitions(ready_skills)

        # Lái provider + model theo lựa chọn của request. Nếu provider không được đăng ký
        # thì rơi về default_provider và BỎ luôn model override (tránh gửi model của
        # provider này sang provider khác gây lỗi). Nếu provider hợp lệ thì honor model.
        requested_provider = config["configurable"].get("provider")
        requested_model = config["configurable"].get("model")
        run_config = config["configurable"].get("run_config") or {}
        if requested_provider in llm_gateway.providers:
            provider = requested_provider
            options = LLMRequestOptions(
                model=requested_model,
                temperature=run_config.get("temperature"),
                max_tokens=run_config.get("max_tokens"),
                timeout_seconds=run_config.get("timeout_seconds"),
            )
        else:
            provider = None
            options = LLMRequestOptions(
                temperature=run_config.get("temperature"),
                max_tokens=run_config.get("max_tokens"),
                timeout_seconds=run_config.get("timeout_seconds"),
            )

        context_plan = compact_messages_if_needed(
            state.messages,
            system_prompt=system_prompt,
            tools=llm_tools,
            context_window_tokens=int(run_config.get("context_window_tokens", 200_000)),
            trigger_ratio=float(run_config.get("context_compaction_trigger_ratio", 0.65)),
            target_ratio=float(run_config.get("context_compaction_target_ratio", 0.45)),
        )

        try:
            response = await AgentNodes._invoke_llm_gateway_with_text_stream(
                llm_gateway=llm_gateway,
                messages=context_plan.messages,
                system_prompt=system_prompt,
                llm_tools=llm_tools,
                provider=provider,
                options=options,
            )
            summary = (
                response.content
                if response.content
                else f"AI selected {len(response.tool_calls)} tool(s)."
            )
            RunStepRepository.complete_step(
                db=db,
                step_id=step.id,
                status="completed",
                summary=summary,
                metadata={
                    "usage": response.usage.model_dump(),
                    "model_used": response.model,
                    "context_window": {
                        "original_tokens_estimate": context_plan.original_tokens,
                        "sent_tokens_estimate": context_plan.compacted_tokens,
                        "threshold_tokens": context_plan.threshold_tokens,
                        "compacted": context_plan.was_compacted,
                    },
                },
            )
            # Nối assistant message (kèm tool_calls) vào lịch sử hội thoại.
            # Bắt buộc: provider yêu cầu mỗi tool message phải đứng SAU một assistant
            # message chứa tool_calls tương ứng; nếu thiếu, lượt gọi LLM kế tiếp sẽ lỗi 400.
            assistant_message = LLMMessage(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
            return {
                "messages": [assistant_message],
                "latest_response": response,
                "current_step_index": state.current_step_index + 1,
            }
        except Exception as exc:
            RunStepRepository.complete_step(
                db=db, step_id=step.id, status="failed", summary=str(exc)
            )
            return {"execution_error": str(exc)}

    @staticmethod
    async def execute_tools(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Execute approved/read-only built-in tools."""
        db = config["configurable"]["db"]
        run_uuid = uuid.UUID(state.run_id)
        session_uuid = uuid.UUID(state.session_id)

        tool_calls_to_run = state.latest_response.tool_calls if state.latest_response else []
        new_tool_messages: list[LLMMessage] = []

        for index, tool_call in enumerate(tool_calls_to_run):
            step = RunStepRepository.create_step(
                db=db,
                run_id=run_uuid,
                step_index=state.current_step_index + index,
                step_type=StepType.TOOL_CALL.value,
                name=f"Skill Runtime: {tool_call.name}",
            )
            RunStepRepository.start_step(db, step.id)

            if tool_call.name not in BUILTIN_TOOL_NAMES:
                error_msg = f"Tool '{tool_call.name}' is not available."
                RunStepRepository.complete_step(
                    db=db, step_id=step.id, status="failed", summary=error_msg
                )
                return {"execution_error": error_msg}

            connector_name = connector_name_for(tool_call.name)
            risk_level = classify_builtin_risk(tool_call.name, tool_call.arguments)
            if risk_level == "prohibited":
                error_msg = f"Tool '{tool_call.name}' call is prohibited by the safety policy."
                RunStepRepository.complete_step(
                    db=db, step_id=step.id, status="failed", summary=error_msg
                )
                return {"execution_error": error_msg}
            if risk_level == "dangerous_action":
                error_msg = (
                    f"Tool '{tool_call.name}' requires human approval and cannot be executed directly."
                )
                RunStepRepository.complete_step(
                    db=db, step_id=step.id, status="failed", summary=error_msg
                )
                return {"execution_error": error_msg}

            db_tool_call = ToolCallRepository.create_tool_call(
                db=db,
                run_id=run_uuid,
                run_step_id=step.id,
                skill_name=tool_call.name,
                skill_source="internal",
                connector_name=connector_name,
                arguments=tool_call.arguments,
                risk_level=risk_level,
                requires_approval=False,
                provider_tool_call_id=tool_call.id,
            )
            ToolCallRepository.start_execution(db, db_tool_call.id)

            started_at = datetime.now(UTC)
            try:
                settings = config["configurable"].get("settings")
                if settings is not None:
                    output, was_truncated = await execute_builtin_tool(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        db=db,
                        settings=settings,
                    )
                else:
                    output, was_truncated = await execute_builtin_tool(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        db=db,
                    )
                status = "completed"
                error_message = None
            except TelecomAgentException as exc:
                output = exc.message
                was_truncated = False
                status = "failed"
                error_message = exc.message

            latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            ToolCallRepository.save_result(
                db=db,
                tool_call_id=db_tool_call.id,
                status=status,
                result={"output": output},
                latency_ms=latency_ms,
                error_msg=error_message,
                output_truncated=was_truncated,
            )
            RunStepRepository.complete_step(db=db, step_id=step.id, status=status, summary=output)

            new_tool_messages.append(
                LLMMessage(
                    role=MessageRole.TOOL,
                    content=output,
                    tool_call_id=tool_call.id,
                    tool_is_error=status == "failed",
                )
            )
            MessageRepository.save_message(
                db=db,
                session_id=session_uuid,
                run_id=run_uuid,
                role="tool",
                content=output,
                metadata={"tool_name": tool_call.name, "tool_call_id": tool_call.id},
            )

        return {
            "messages": new_tool_messages,
            "current_step_index": state.current_step_index + len(tool_calls_to_run),
        }
