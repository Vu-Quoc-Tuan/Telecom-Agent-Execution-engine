from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from app.agent.builtin_runners import execute_builtin_tool
from app.agent.builtin_tools import (
    BUILTIN_TOOL_NAMES,
    LOAD_SKILL,
    build_builtin_tool_definitions,
    classify_builtin_risk,
    connector_name_for,
)
from app.agent.context_window import compact_messages_if_needed
from app.agent.prompts import build_system_prompt
from app.agent.safety import AgentSafetyGuard
from app.agent.state import AgentState
from app.agent.tool_validation import validate_tool_call_arguments
from app.common.enums import StepType
from app.common.exceptions import TelecomAgentException
from app.database.repositories.approvals import ApprovalRepository
from app.database.repositories.messages import MessageRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.skills import SkillRepository
from app.database.repositories.tool_calls import ToolCallRepository
from app.llm.gateway import LLMGateway
from app.llm.schemas import (
    LLMMessage,
    LLMRequestOptions,
    MessageRole,
    StreamEventType,
    ToolChoice,
    ToolChoiceMode,
)
from app.observability.langfuse import telemetry_tracker


def _normalize_provider_name(provider: Any) -> str | None:
    if not isinstance(provider, str):
        return None
    normalized = provider.strip().lower()
    return normalized or None


def _positive_int_config(
    config: dict[str, Any],
    key: str,
    default: int | None = None,
) -> int | None:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float_config(
    config: dict[str, Any],
    key: str,
    default: float | None = None,
) -> float | None:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _bounded_float_config(
    config: dict[str, Any],
    key: str,
    default: float | None = None,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def _skill_was_loaded(messages: list[LLMMessage], skill_name: str) -> bool:
    return any(
        tool_call.name == LOAD_SKILL and tool_call.arguments.get("skill_name") == skill_name
        for message in messages
        if message.role is MessageRole.ASSISTANT
        for tool_call in message.tool_calls
    )


def _sandbox_available(settings: Any) -> bool:
    from app.sandbox.docker_executor import sandbox_available

    return sandbox_available(settings)


class AgentNodes:
    @staticmethod
    def _record_tool_validation_error(
        *,
        db,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        tool_call,
        error_message: str,
        error_code: str = "TOOL_VALIDATION_ERROR",
        guidance: str | None = None,
    ) -> LLMMessage:
        retry_guidance = guidance or (
            "Correct the tool arguments and retry. For SSH, send exactly one command "
            "per tool call without &&, ;, command substitution, or multi-stage pipes."
        )
        output = json.dumps(
            {
                "status": "failed",
                "code": error_code,
                "message": error_message,
                "guidance": retry_guidance,
            },
            ensure_ascii=False,
        )
        RunStepRepository.complete_step(
            db=db,
            step_id=step_id,
            status="failed",
            summary=error_message,
        )
        MessageRepository.save_message(
            db=db,
            session_id=session_id,
            run_id=run_id,
            role=MessageRole.TOOL.value,
            content=output,
            metadata={
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "error_code": error_code,
            },
        )
        return LLMMessage(
            role=MessageRole.TOOL,
            content=output,
            tool_call_id=tool_call.id,
            tool_is_error=True,
        )

    @staticmethod
    async def _execute_and_log_single_tool(
        *,
        db,
        run_uuid: uuid.UUID,
        session_uuid: uuid.UUID,
        step_id: uuid.UUID,
        tool_call,
        risk_level: str,
        settings,
        idempotency_key: str | None = None,
        step_index: int = 0,
    ) -> LLMMessage:
        db_tool_call = ToolCallRepository.create_tool_call(
            db=db,
            run_id=run_uuid,
            run_step_id=step_id,
            skill_name=tool_call.name,
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
        RunStepRepository.complete_step(db=db, step_id=step_id, status=status, summary=output)

        # Ghi tool span con vào Langfuse dưới root span của lượt chạy hiện tại.
        # QUAN TRỌNG: dùng run_uuid.hex (giống trace_run_start) để _ensure_root
        # tìm đúng root span đã tạo, tránh tạo orphan observation.
        try:
            turn_index = telemetry_tracker.get_turn_index(run_uuid.hex)
            telemetry_tracker.trace_span(
                run_id=run_uuid.hex,
                span_name=f"tool: {tool_call.name} #{turn_index}.{step_index}",
                input_data=tool_call.arguments,
                output_data=output,
                start_time=started_at,
                end_time=datetime.now(UTC),
                status=status,
            )
        except Exception:
            pass

        MessageRepository.save_message(
            db=db,
            session_id=session_uuid,
            run_id=run_uuid,
            role="tool",
            content=output,
            metadata={"tool_name": tool_call.name, "tool_call_id": tool_call.id},
        )
        return LLMMessage(
            role=MessageRole.TOOL,
            content=output,
            tool_call_id=tool_call.id,
            tool_is_error=status == "failed",
        )

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
        fallback_providers: list[str] | None = None,
        provider_options: dict[str, LLMRequestOptions] | None = None,
        provider_kwargs: dict[str, Any] | None = None,
    ):
        call_kwargs = provider_kwargs or {}
        stream_method = getattr(llm_gateway, "stream", None)
        if not callable(stream_method):
            return await llm_gateway.invoke(
                messages=messages,
                system_prompt=system_prompt,
                tools=llm_tools,
                provider=provider,
                options=options,
                fallback_providers=fallback_providers,
                fallback_on_non_retryable=True,
                provider_options=provider_options,
                **call_kwargs,
            )

        writer = AgentNodes._custom_stream_writer()
        final_response = None
        async for stream_chunk in stream_method(
            messages=messages,
            system_prompt=system_prompt,
            tools=llm_tools,
            provider=provider,
            options=options,
            fallback_providers=fallback_providers,
            fallback_on_non_retryable=True,
            provider_options=provider_options,
            **call_kwargs,
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
            fallback_providers=fallback_providers,
            fallback_on_non_retryable=True,
            provider_options=provider_options,
            **call_kwargs,
        )

    @staticmethod
    async def suspend_for_human(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Persist an approval batch, pause the graph, then append resumed tool messages."""
        db = config["configurable"]["db"]
        run_uuid = uuid.UUID(state.run_id)
        session_uuid = uuid.UUID(state.session_id)
        tool_calls = state.latest_response.tool_calls if state.latest_response else []
        executed_tool_messages: dict[str, LLMMessage] = {}
        settings = config["configurable"].get("settings")
        tool_catalog = build_builtin_tool_definitions(
            SkillRepository.list_ready_skills(db),
            sandbox_available=_sandbox_available(settings),
            settings=settings,
        )

        for index, tool_call in enumerate(tool_calls):
            idempotency_key = f"approval:{run_uuid}:{tool_call.id}"
            existing = ToolCallRepository.get_by_idempotency_key(db, idempotency_key)
            if existing is not None:
                continue

            try:
                validate_tool_call_arguments(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    tools=tool_catalog,
                )
            except TelecomAgentException as exc:
                RunStepRepository.create_error_step(
                    db=db,
                    run_id=run_uuid,
                    summary=exc.message,
                    metadata={"tool_name": tool_call.name, "tool_call_id": tool_call.id},
                )
                return {"execution_error": exc.message}

            try:
                risk_level = classify_builtin_risk(tool_call.name, tool_call.arguments)
            except TelecomAgentException as exc:
                RunStepRepository.create_error_step(
                    db=db,
                    run_id=run_uuid,
                    summary=exc.message,
                    metadata={"tool_name": tool_call.name, "tool_call_id": tool_call.id},
                )
                return {"execution_error": exc.message}
            if risk_level == "require_approval":
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
            executed_tool_messages[tool_call.id] = await AgentNodes._execute_and_log_single_tool(
                db=db,
                run_uuid=run_uuid,
                session_uuid=session_uuid,
                step_id=step.id,
                tool_call=tool_call,
                risk_level=risk_level,
                settings=settings,
                idempotency_key=idempotency_key,
                step_index=state.current_step_index + index,
            )

        resume_payload = interrupt(
            {
                "run_id": state.run_id,
                "tool_call_ids": [tool_call.id for tool_call in tool_calls],
            }
        )
        resumed_tool_messages = {
            message.tool_call_id: message
            for message in (
                LLMMessage.model_validate(message) for message in resume_payload.get("messages", [])
            )
            if message.tool_call_id
        }
        merged_tool_messages = {
            **executed_tool_messages,
            **resumed_tool_messages,
        }
        return {
            "messages": [
                merged_tool_messages[tool_call.id]
                for tool_call in tool_calls
                if tool_call.id in merged_tool_messages
            ],
            "current_step_index": state.current_step_index + len(tool_calls),
            "approval_rejected": bool(resume_payload.get("approval_rejected", False)),
        }

    @staticmethod
    async def fail_unsafe_or_exhausted(state: AgentState) -> dict[str, Any]:
        if state.current_step_index >= state.max_steps:
            message = f"Agent exceeded the maximum of {state.max_steps} steps."
        else:
            message = "Agent requested an unavailable tool or failed safety validation."
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
        run_config = config["configurable"].get("run_config") or {}
        selected_skill = run_config.get("selected_skill")
        if not isinstance(selected_skill, str) or not selected_skill.strip():
            selected_skill = None
        else:
            selected_skill = selected_skill.strip()

        ready_skills = SkillRepository.list_ready_skills(db)
        if selected_skill:
            ready_skills = [skill for skill in ready_skills if skill.name == selected_skill]
            if not ready_skills:
                message = f"Selected skill '{selected_skill}' is no longer ready."
                RunStepRepository.complete_step(
                    db=db,
                    step_id=step.id,
                    status="failed",
                    summary=message,
                )
                return {"execution_error": message}
        settings = config["configurable"].get("settings")
        system_prompt = build_system_prompt(
            ready_skills,
            settings=settings,
            selected_skill_name=selected_skill,
        )
        sandbox_available = _sandbox_available(settings)
        llm_tools = build_builtin_tool_definitions(
            ready_skills,
            sandbox_available=sandbox_available,
            settings=settings,
        )
        pending_interventions = MessageRepository.list_pending_interventions(db, run_uuid)
        intervention_messages = [
            LLMMessage(
                role=MessageRole.USER,
                content=(
                    "[OPERATOR INTERVENTION]\n"
                    + AgentSafetyGuard.sanitize_input_prompt(message.content)
                ),
            )
            for message in pending_interventions
        ]
        messages_for_llm = [*state.messages, *intervention_messages]

        requested_provider = config["configurable"].get("provider")
        requested_model = config["configurable"].get("model")
        normalized_provider = _normalize_provider_name(requested_provider)
        option_values: dict[str, Any] = {
            "temperature": _bounded_float_config(
                run_config, "temperature", minimum=0.0, maximum=2.0
            ),
            "max_tokens": _positive_int_config(run_config, "max_tokens"),
            "timeout_seconds": _positive_float_config(run_config, "timeout_seconds"),
        }
        if normalized_provider in set(llm_gateway.providers):
            provider = normalized_provider
            option_values["model"] = requested_model
        else:
            provider = None
        options = LLMRequestOptions(**option_values)

        if (
            selected_skill
            and ready_skills
            and not _skill_was_loaded(state.messages, selected_skill)
        ):
            options.tool_choice = ToolChoice(
                mode=ToolChoiceMode.SPECIFIC,
                tool_name=LOAD_SKILL,
            )
            options.parallel_tool_calls = False

        if state.approval_rejected:
            options.tool_choice = ToolChoice(mode=ToolChoiceMode.NONE)
            options.parallel_tool_calls = False

        fallback_providers: list[str] = []
        provider_options: dict[str, LLMRequestOptions] = {}
        if provider:
            fallback_providers = [
                candidate for candidate in llm_gateway.providers if candidate != provider
            ]
            get_adapter = getattr(llm_gateway, "get_adapter", None)
            if callable(get_adapter):
                for fallback_provider in fallback_providers:
                    fallback_model = get_adapter(fallback_provider).model
                    provider_options[fallback_provider] = options.model_copy(
                        update={"model": fallback_model}
                    )

        trigger_ratio = _bounded_float_config(
            run_config,
            "context_compaction_trigger_ratio",
            0.65,
            minimum=0.0,
            maximum=1.0,
        )

        target_ratio = _bounded_float_config(
            run_config,
            "context_compaction_target_ratio",
            0.45,
            minimum=0.0,
            maximum=1.0,
        )

        context_window_tokens = _positive_int_config(run_config, "context_window_tokens", 200_000)

        context_plan = compact_messages_if_needed(
            messages_for_llm,
            system_prompt=system_prompt,
            tools=llm_tools,
            context_window_tokens=context_window_tokens,
            trigger_ratio=trigger_ratio,
            target_ratio=target_ratio,
        )

        langfuse_callback = telemetry_tracker.get_langchain_callback_handler(str(run_uuid))
        provider_kwargs: dict[str, Any] = {}
        if langfuse_callback is not None:
            turn_index = telemetry_tracker.get_turn_index(str(run_uuid))
            provider_kwargs = {
                "callbacks": [langfuse_callback],
                "run_name": f"AI Core Reasoner #{turn_index}.{state.current_step_index}",
                "metadata": {
                    "run_id": str(run_uuid),
                    "step_id": str(step.id),
                },
                "tags": ["telecom-agent", "llm"],
            }
        try:
            response = await AgentNodes._invoke_llm_gateway_with_text_stream(
                llm_gateway=llm_gateway,
                messages=context_plan.messages,
                system_prompt=system_prompt,
                llm_tools=llm_tools,
                provider=provider,
                options=options,
                fallback_providers=fallback_providers,
                provider_options=provider_options,
                provider_kwargs=provider_kwargs,
            )
            summary = (
                response.content
                if response.content
                else f"AI selected {len(response.tool_calls)} tool(s)."
            )
            assistant_message = LLMMessage(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
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
                    "operator_interventions": [
                        str(message.id) for message in pending_interventions
                    ],
                },
                commit=False,
            )
            MessageRepository.mark_interventions_injected(
                db,
                [message.id for message in pending_interventions],
                commit=False,
            )
            db.commit()

            # Nối assistant message (kèm tool_calls) vào lịch sử hội thoại.
            # Bắt buộc: provider yêu cầu mỗi tool message phải đứng SAU một assistant
            # message chứa tool_calls tương ứng; nếu thiếu, lượt gọi LLM kế tiếp sẽ lỗi 400.
            return {
                "messages": [*intervention_messages, assistant_message],
                "latest_response": response,
                "current_step_index": state.current_step_index + 1,
            }
        except Exception as exc:
            db.rollback()
            RunStepRepository.complete_step(
                db=db, step_id=step.id, status="failed", summary=str(exc)
            )
            return {"execution_error": str(exc)}

    @staticmethod
    async def execute_tools(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Execute built-in tools whose mode is auto_execute."""
        db = config["configurable"]["db"]
        run_uuid = uuid.UUID(state.run_id)
        session_uuid = uuid.UUID(state.session_id)

        tool_calls_to_run = state.latest_response.tool_calls if state.latest_response else []
        new_tool_messages: list[LLMMessage] = []
        settings = config["configurable"].get("settings")
        sandbox_available = _sandbox_available(settings)
        ready_skills = SkillRepository.list_ready_skills(db)
        ready_skill_names = {s.name for s in ready_skills}
        tool_catalog = build_builtin_tool_definitions(
            ready_skills,
            sandbox_available=sandbox_available,
            settings=settings,
        )

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
                if tool_call.name in ready_skill_names:
                    error_msg = (
                        f"Tool '{tool_call.name}' is a skill name, not a built-in tool. "
                        f"To use/execute this skill, you must first call the 'load_skill' tool "
                        f"with argument skill_name='{tool_call.name}' to load its documentation/files."
                    )
                else:
                    error_msg = f"Tool '{tool_call.name}' is not available."
                new_tool_messages.append(
                    AgentNodes._record_tool_validation_error(
                        db=db,
                        session_id=session_uuid,
                        run_id=run_uuid,
                        step_id=step.id,
                        tool_call=tool_call,
                        error_message=error_msg,
                    )
                )
                continue

            try:
                validate_tool_call_arguments(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    tools=tool_catalog,
                )
            except TelecomAgentException as exc:
                new_tool_messages.append(
                    AgentNodes._record_tool_validation_error(
                        db=db,
                        session_id=session_uuid,
                        run_id=run_uuid,
                        step_id=step.id,
                        tool_call=tool_call,
                        error_message=exc.message,
                    )
                )
                continue

            try:
                risk_level = classify_builtin_risk(tool_call.name, tool_call.arguments)
            except TelecomAgentException as exc:
                new_tool_messages.append(
                    AgentNodes._record_tool_validation_error(
                        db=db,
                        session_id=session_uuid,
                        run_id=run_uuid,
                        step_id=step.id,
                        tool_call=tool_call,
                        error_message=exc.message,
                    )
                )
                continue
            if risk_level == "require_approval":
                error_msg = f"Tool '{tool_call.name}' requires human approval and cannot be executed directly."
                new_tool_messages.append(
                    AgentNodes._record_tool_validation_error(
                        db=db,
                        session_id=session_uuid,
                        run_id=run_uuid,
                        step_id=step.id,
                        tool_call=tool_call,
                        error_message=error_msg,
                        error_code="TOOL_REQUIRES_APPROVAL",
                        guidance=(
                            "Retry this state-changing action as a separate tool call so the "
                            "runtime can open the human approval flow."
                        ),
                    )
                )
                continue

            msg = await AgentNodes._execute_and_log_single_tool(
                db=db,
                run_uuid=run_uuid,
                session_uuid=session_uuid,
                step_id=step.id,
                tool_call=tool_call,
                risk_level=risk_level,
                settings=settings,
                step_index=state.current_step_index + index,
            )
            new_tool_messages.append(msg)

        return {
            "messages": new_tool_messages,
            "current_step_index": state.current_step_index + len(tool_calls_to_run),
        }
