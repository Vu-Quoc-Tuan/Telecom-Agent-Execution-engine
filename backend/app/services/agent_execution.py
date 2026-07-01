from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.builtin_runners import execute_builtin_tool
from app.agent.graph import build_telecom_agent
from app.agent.prompts import TELECOM_AGENT_PROMPT_VERSION
from app.agent.safety import AgentSafetyGuard
from app.common.enums import RunStatus
from app.common.exceptions import TelecomAgentException
from app.config import settings as app_settings
from app.database.repositories.approvals import ApprovalRepository
from app.database.repositories.messages import MessageRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.runs import RunRepository
from app.database.repositories.sessions import SessionRepository
from app.database.repositories.skills import SkillRepository
from app.database.repositories.tool_calls import ToolCallRepository
from app.llm.gateway import LLMGateway
from app.llm.schemas import LLMMessage, LLMRequestOptions, MessageRole
from app.observability.langfuse import telemetry_tracker
from app.observability.tracing import TelecomTaskTracer
from app.services.timeline import serialize_timeline_steps


class AgentExecutionService:
    _agent_app = None

    @classmethod
    def configure(cls, agent_app=None):
        if agent_app is not None:
            cls._agent_app = agent_app

    @classmethod
    def get_agent_app(cls):
        if cls._agent_app is None:
            cls._agent_app = build_telecom_agent()
        return cls._agent_app

    @classmethod
    async def run_agent_lifecycle(
        cls,
        db: Session,
        llm_gateway: LLMGateway,
        session_id: uuid.UUID,
        user_content: str,
        provider: str = "openai",
        model: str = "gpt-4o",
        selected_skill: str | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        session = SessionRepository.get_session_by_id(db, session_id)
        if not session:
            yield "error", {"message": "Session does not exist or has been deleted."}
            return

        if selected_skill:
            skill = SkillRepository.get_skill_by_name(db, selected_skill)
            if not skill or skill.status != "ready":
                yield (
                    "error",
                    {"message": f"Skill '{selected_skill}' does not exist or is not ready."},
                )
                return

        initial_run_config = cls._default_run_config()
        if selected_skill:
            initial_run_config["selected_skill"] = selected_skill

        user_db_msg = MessageRepository.save_message(
            db=db,
            session_id=session_id,
            run_id=None,
            role="user",
            content=user_content,
        )
        run_record = RunRepository.create_run(
            db=db,
            session_id=session_id,
            provider=provider,
            model=model,
            config_dict=initial_run_config,
            prompt_version=telemetry_tracker.get_active_prompt_version(
                TELECOM_AGENT_PROMPT_VERSION
            ),
        )
        user_db_msg.run_id = run_record.id
        db.commit()
        MessageRepository.requeue_undelivered_interventions(
            db,
            session_id=session_id,
            run_id=run_record.id,
        )
        session_history = MessageRepository.get_chat_history(db, session_id)

        yield (
            "run_started",
            {"run_id": str(run_record.id), "session_id": str(session_id), "status": "running"},
        )

        trace_id = run_record.id.hex
        # 🛡️ DLP: che mặt nạ secret/PII trước khi nội dung rời hệ thống sang LLM bên thứ 3.
        # Bản gốc người dùng gõ vẫn được lưu nguyên trong chat_messages ở trên để hiển thị.
        sanitized_content = AgentSafetyGuard.sanitize_input_prompt(user_content)
        telemetry_tracker.trace_run_start(
            session_id=str(session_id),
            run_id=trace_id,
            input_content=sanitized_content,
        )

        run_config = cls._run_config(run_record)
        graph_config = cls._graph_config(
            db,
            llm_gateway,
            session_id,
            run_id=run_record.id,
            provider=provider,
            model=model,
            run_config=run_config,
        )
        initial_state = {
            "messages": cls._llm_messages_from_history(session_history),
            "session_id": str(session_id),
            "run_id": str(run_record.id),
            "current_step_index": 0,
            "max_steps": cls._int_config(run_config, "max_steps", app_settings.AGENT_MAX_STEPS),
            "execution_error": None,
            "latest_response": None,
        }

        agent_app = cls.get_agent_app()
        try:
            with TelecomTaskTracer("agent_run", session_id=str(session_id), run_id=trace_id):
                async for chunk in agent_app.astream(
                    initial_state, config=graph_config, stream_mode=["updates", "custom"]
                ):
                    stream_mode, stream_payload = cls._normalize_graph_stream_chunk(chunk)
                    if stream_mode == "custom":
                        text_delta = cls._text_delta_payload(
                            stream_payload, run_id=str(run_record.id)
                        )
                        if text_delta is not None:
                            yield "text_delta", text_delta
                        continue

                    node_name = next(iter(stream_payload.keys()))
                    RunRepository.increment_step_count(db, run_record.id)
                    yield (
                        "timeline_updated",
                        {
                            "run_id": str(run_record.id),
                            "last_executed_node": node_name,
                            "steps": cls._serialize_steps(db, run_record.id),
                        },
                    )

                final_graph_state = await cls._get_graph_state(agent_app, config=graph_config)
                latest_response = final_graph_state.values.get("latest_response")
                execution_error = final_graph_state.values.get("execution_error")

                if execution_error:
                    error_message = cls._mark_run_failed(
                        db,
                        run_record.id,
                        error_message=str(execution_error),
                        source="agent_graph",
                    )
                    telemetry_tracker.trace_run_end(
                        run_id=trace_id,
                        output_content=error_message,
                        status="failed",
                    )
                    yield (
                        "run_failed",
                        {
                            "run_id": str(run_record.id),
                            "error": error_message,
                        },
                    )
                    return

                ApprovalRepository.expire_pending_requests(db, run_id=run_record.id)
                pending_approvals = [
                    approval
                    for approval in ApprovalRepository.get_pending_requests(db)
                    if approval.run_id == run_record.id
                ]
                if pending_approvals:
                    RunRepository.update_run_status(
                        db, run_record.id, status=RunStatus.WAITING_APPROVAL.value
                    )
                    approval = pending_approvals[0]
                    tool_call = ToolCallRepository.get_tool_call(db, approval.tool_call_id)
                    yield (
                        "run_suspended",
                        cls._build_run_suspended_event(run_record.id, approval, tool_call),
                    )
                    return

                if latest_response:
                    assistant_text = latest_response.content or "Tác vụ hoàn thành."
                    updated_run = RunRepository.update_run_status(
                        db, run_record.id, status=RunStatus.COMPLETED.value
                    )
                    terminal_error = cls._terminal_error_message(updated_run)
                    if terminal_error:
                        telemetry_tracker.trace_run_end(
                            run_id=trace_id,
                            output_content=terminal_error,
                            status="failed",
                        )
                        yield (
                            "run_failed",
                            {"run_id": str(run_record.id), "error": terminal_error},
                        )
                        return
                    MessageRepository.save_message(
                        db=db,
                        session_id=session_id,
                        run_id=run_record.id,
                        role="assistant",
                        content=assistant_text,
                    )
                    # Tự động cập nhật tiêu đề cuộc chat nếu tiêu đề hiện tại là mặc định ("New Session" hoặc trống)
                    session_title = getattr(session, "title", None)
                    if (
                        session_title is None
                        or session_title.strip() == ""
                        or session_title.strip() == "New Session"
                    ):
                        try:
                            # Hỏi LLM sinh tiêu đề ngắn gọn
                            title_prompt = (
                                "Tóm tắt câu hỏi hoặc yêu cầu sau thành một tiêu đề ngắn gọn, súc tích (khoảng 3-6 từ), "
                                "chuyên nghiệp để đặt tên cho cuộc chat viễn thông này. Chỉ trả về đúng tiêu đề, "
                                f'không thêm bất kỳ từ giải thích nào khác:\n\n"{sanitized_content}"'
                            )
                            # Đồng bộ hóa logic chọn provider giống như trong nodes.py.
                            target_provider = provider.strip().lower() if provider else None
                            if target_provider in llm_gateway.providers:
                                options = LLMRequestOptions(model=model, temperature=0.1)
                            else:
                                target_provider = None
                                options = LLMRequestOptions(temperature=0.1)

                            title_resp = await llm_gateway.invoke(
                                provider=target_provider,
                                messages=[LLMMessage(role=MessageRole.USER, content=title_prompt)],
                                options=options,
                            )
                            if title_resp and title_resp.content:
                                new_title = title_resp.content.strip().strip('"').strip("'").strip()
                                if new_title:
                                    if len(new_title) > 50:
                                        new_title = new_title[:47] + "..."
                                    if hasattr(session, "title"):
                                        session.title = new_title
                                        db.commit()
                        except Exception as e:
                            import logging

                            logging.getLogger("telecom-agent").error(
                                f"Title auto-update failed: {e}", exc_info=True
                            )
                    telemetry_tracker.trace_run_end(
                        run_id=trace_id,
                        output_content=assistant_text,
                        status="completed",
                    )
                    yield (
                        "run_completed",
                        {"run_id": str(run_record.id), "final_answer": assistant_text},
                    )
                    return

                error_message = "Agent graph ended without a final response."
                error_message = cls._mark_run_failed(
                    db,
                    run_record.id,
                    error_message=error_message,
                    source="agent_graph",
                )
                telemetry_tracker.trace_run_end(
                    run_id=trace_id,
                    output_content=error_message,
                    status="failed",
                )
                yield "run_failed", {"run_id": str(run_record.id), "error": error_message}
        except Exception as exc:
            error_message = cls._mark_run_failed(
                db,
                run_record.id,
                error_message=str(exc),
                source="agent_lifecycle",
            )
            telemetry_tracker.trace_run_end(
                run_id=trace_id,
                output_content=error_message,
                status="failed",
            )
            yield "run_failed", {"run_id": str(run_record.id), "error": error_message}

    @classmethod
    async def resolve_approval_and_resume_lifecycle(
        cls,
        db: Session,
        llm_gateway: LLMGateway,
        approval_id: uuid.UUID,
        action: str,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        if action not in {"approved", "rejected"}:
            yield "error", {"message": "Approval action must be 'approved' or 'rejected'."}
            return

        started_at = datetime.now(UTC)
        pending_approval = ApprovalRepository.get_request(db, approval_id)
        if not pending_approval or pending_approval.status != "pending":
            yield "error", {"message": "Approval request is invalid or already resolved."}
            return

        run_record = RunRepository.get_run(db, pending_approval.run_id)
        tool_call = ToolCallRepository.get_tool_call(db, pending_approval.tool_call_id)
        if not run_record or not tool_call:
            yield "error", {"message": "Approval request is missing its run or tool call."}
            return
        if RunRepository.is_terminal_status(run_record.status):
            yield (
                "error",
                {"message": f"Run is already terminal with status '{run_record.status}'."},
            )
            return

        approval = ApprovalRepository.resolve_request(
            db,
            approval_id,
            status=action,
        )
        if approval is None:
            yield "error", {"message": "Approval expired or was resolved concurrently."}
            return

        session_id = run_record.session_id
        if action == "rejected":
            output = json.dumps(
                {
                    "status": "rejected",
                    "code": "HUMAN_REJECTED",
                    "message": "The human operator rejected this tool call. It was not executed.",
                    "reason": "Rejected by operator.",
                },
                ensure_ascii=False,
            )
            RunStepRepository.complete_step(
                db=db, step_id=tool_call.run_step_id, status="failed", summary=output
            )
            ToolCallRepository.save_result(
                db=db,
                tool_call_id=tool_call.id,
                status="rejected",
                result={"output": output},
                latency_ms=0,
                error_msg=output,
            )
            MessageRepository.save_message(
                db=db,
                session_id=session_id,
                run_id=run_record.id,
                role="tool",
                content=output,
                metadata={
                    "tool_name": tool_call.skill_name,
                    "tool_call_id": tool_call.provider_tool_call_id,
                    "approval_status": "rejected",
                },
            )
        else:
            ToolCallRepository.start_execution(db, tool_call.id)
            started_at = datetime.now(UTC)
            try:
                output, was_truncated = await execute_builtin_tool(
                    tool_name=tool_call.skill_name,
                    arguments=tool_call.arguments_json,
                    db=db,
                    settings=app_settings,
                    approval_confirmations=1,
                )
                status = "completed"
                error_message = None
            except TelecomAgentException as exc:
                output = exc.message
                was_truncated = False
                status = "failed"
                error_message = exc.message
            except Exception as exc:
                output = str(exc) or type(exc).__name__
                latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
                ToolCallRepository.save_result(
                    db=db,
                    tool_call_id=tool_call.id,
                    status="failed",
                    result={"output": output},
                    latency_ms=latency_ms,
                    error_msg=output,
                )
                RunStepRepository.complete_step(
                    db=db,
                    step_id=tool_call.run_step_id,
                    status="failed",
                    summary=output,
                )
                error_message = cls._mark_run_failed(
                    db,
                    run_record.id,
                    error_message=output,
                    source="approved_tool_execution",
                )
                yield "run_failed", {"run_id": str(run_record.id), "error": error_message}
                return

            latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            ToolCallRepository.save_result(
                db=db,
                tool_call_id=tool_call.id,
                status=status,
                result={"output": output},
                latency_ms=latency_ms,
                error_msg=error_message,
                output_truncated=was_truncated,
            )
            RunStepRepository.complete_step(
                db=db, step_id=tool_call.run_step_id, status=status, summary=output
            )
            MessageRepository.save_message(
                db=db, session_id=session_id, run_id=run_record.id, role="tool", content=output
            )
        run_config = cls._run_config(run_record)
        graph_config = cls._graph_config(
            db,
            llm_gateway,
            session_id,
            run_id=run_record.id,
            provider=run_record.provider,
            model=run_record.model,
            run_config=run_config,
        )
        agent_app = cls.get_agent_app()

        ApprovalRepository.expire_pending_requests(db, run_id=run_record.id)
        remaining_pending = [
            request
            for request in ApprovalRepository.get_pending_requests(db)
            if request.run_id == run_record.id
        ]
        if remaining_pending:
            next_approval = remaining_pending[0]
            tool_call = ToolCallRepository.get_tool_call(db, next_approval.tool_call_id)
            RunRepository.update_run_status(
                db,
                run_record.id,
                status=RunStatus.WAITING_APPROVAL.value,
            )
            yield (
                "run_suspended",
                cls._build_run_suspended_event(run_record.id, next_approval, tool_call),
            )
            return

        batch_requests = ApprovalRepository.get_requests_by_run(db, run_record.id)
        if any(request.status in {"expired", "cancelled"} for request in batch_requests):
            error_message = "Approval batch expired or was cancelled before completion."
            error_message = cls._mark_run_failed(
                db,
                run_record.id,
                error_message=error_message,
                source="approval_batch",
            )
            yield "run_failed", {"run_id": str(run_record.id), "error": error_message}
            return

        final_graph_state = await cls._get_graph_state(agent_app, config=graph_config)
        latest_tool_calls = final_graph_state.values.get("latest_response")
        provider_ids = (
            [call.id for call in latest_tool_calls.tool_calls] if latest_tool_calls else []
        )
        persisted_calls = {
            call.provider_tool_call_id: call
            for call in ToolCallRepository.get_tool_calls_by_run(db, run_record.id)
            if call.provider_tool_call_id in provider_ids
        }
        if len(persisted_calls) != len(provider_ids):
            error_message = cls._mark_run_failed(
                db,
                run_record.id,
                error_message="Approval batch is incomplete and cannot be resumed.",
                source="approval_batch",
            )
            yield "run_failed", {"run_id": str(run_record.id), "error": error_message}
            return

        new_tool_messages = [
            LLMMessage(
                role=MessageRole.TOOL,
                content=str((persisted_calls[provider_id].result_json or {}).get("output", "")),
                tool_call_id=provider_id,
                tool_is_error=persisted_calls[provider_id].status != "completed",
            )
            for provider_id in provider_ids
        ]

        RunRepository.update_run_status(db, run_record.id, status=RunStatus.RUNNING.value)
        yield "run_resumed", {"run_id": str(run_record.id), "action_taken": action}
        try:
            with TelecomTaskTracer(
                "agent_resume", session_id=str(session_id), run_id=str(run_record.id)
            ):
                async for chunk in agent_app.astream(
                    Command(
                        resume={
                            "messages": [
                                message.model_dump(mode="json") for message in new_tool_messages
                            ],
                            "approval_rejected": any(
                                request.status == "rejected" for request in batch_requests
                            ),
                        }
                    ),
                    config=graph_config,
                    stream_mode=["updates", "custom"],
                ):
                    stream_mode, stream_payload = cls._normalize_graph_stream_chunk(chunk)
                    if stream_mode == "custom":
                        text_delta = cls._text_delta_payload(
                            stream_payload, run_id=str(run_record.id)
                        )
                        if text_delta is not None:
                            yield "text_delta", text_delta
                        continue

                    node_name = next(iter(stream_payload.keys()))
                    RunRepository.increment_step_count(db, run_record.id)
                    yield (
                        "timeline_updated",
                        {
                            "run_id": str(run_record.id),
                            "last_executed_node": node_name,
                            "steps": cls._serialize_steps(db, run_record.id),
                        },
                    )

                final_graph_state = await cls._get_graph_state(agent_app, config=graph_config)
                latest_response = final_graph_state.values.get("latest_response")
                execution_error = final_graph_state.values.get("execution_error")
                if execution_error:
                    error_message = cls._mark_run_failed(
                        db,
                        run_record.id,
                        error_message=str(execution_error),
                        source="agent_resume",
                    )
                    telemetry_tracker.trace_run_end(
                        run_id=run_record.id.hex,
                        output_content=error_message,
                        status="failed",
                    )
                    yield (
                        "run_failed",
                        {
                            "run_id": str(run_record.id),
                            "error": error_message,
                        },
                    )
                    return

                ApprovalRepository.expire_pending_requests(db, run_id=run_record.id)
                pending_approvals = [
                    approval
                    for approval in ApprovalRepository.get_pending_requests(db)
                    if approval.run_id == run_record.id
                ]
                if pending_approvals:
                    RunRepository.update_run_status(
                        db, run_record.id, status=RunStatus.WAITING_APPROVAL.value
                    )
                    approval = pending_approvals[0]
                    tool_call = ToolCallRepository.get_tool_call(db, approval.tool_call_id)
                    yield (
                        "run_suspended",
                        cls._build_run_suspended_event(run_record.id, approval, tool_call),
                    )
                    return
                assistant_text = (
                    latest_response.content
                    if (latest_response and latest_response.content is not None)
                    else "Tác vụ sau phê duyệt xử lý xong."
                )
                updated_run = RunRepository.update_run_status(
                    db, run_record.id, status=RunStatus.COMPLETED.value
                )
                terminal_error = cls._terminal_error_message(updated_run)
                if terminal_error:
                    telemetry_tracker.trace_run_end(
                        run_id=run_record.id.hex,
                        output_content=terminal_error,
                        status="failed",
                    )
                    yield (
                        "run_failed",
                        {"run_id": str(run_record.id), "error": terminal_error},
                    )
                    return
                MessageRepository.save_message(
                    db=db,
                    session_id=session_id,
                    run_id=run_record.id,
                    role="assistant",
                    content=assistant_text,
                )

                telemetry_tracker.trace_run_end(
                    run_id=run_record.id.hex,
                    output_content=assistant_text,
                    status="completed",
                )
                yield (
                    "run_completed",
                    {"run_id": str(run_record.id), "final_answer": assistant_text},
                )
        except Exception as exc:
            error_message = cls._mark_run_failed(
                db,
                run_record.id,
                error_message=str(exc),
                source="agent_resume",
            )
            telemetry_tracker.trace_run_end(
                run_id=run_record.id.hex,
                output_content=error_message,
                status="failed",
            )
            yield "run_failed", {"run_id": str(run_record.id), "error": error_message}

    @classmethod
    def _graph_config(
        cls,
        db: Session,
        llm_gateway: LLMGateway,
        session_id: uuid.UUID,
        *,
        run_id: uuid.UUID,
        provider: str | None = None,
        model: str | None = None,
        run_config: dict[str, Any] | None = None,
    ) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": str(run_id),
                "db": db,
                "llm_gateway": llm_gateway,
                "provider": provider,
                "model": model,
                "run_config": run_config or cls._default_run_config(),
                "settings": app_settings,
            }
        }

    @staticmethod
    def _llm_messages_from_history(history: list[Any]) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        for row in history:
            if row.role not in {MessageRole.USER.value, MessageRole.ASSISTANT.value}:
                continue
            metadata = getattr(row, "metadata_json", None)
            if (
                isinstance(metadata, dict)
                and metadata.get("kind") == "operator_intervention"
                and metadata.get("intervention_status") in {"pending", "undelivered"}
            ):
                continue
            messages.append(
                LLMMessage(
                    role=MessageRole(row.role),
                    content=AgentSafetyGuard.sanitize_input_prompt(str(row.content or "")),
                )
            )
        return messages[-40:]

    @staticmethod
    async def _get_graph_state(agent_app, *, config: RunnableConfig):
        async_get_state = getattr(agent_app, "aget_state", None)
        if async_get_state is not None:
            return await async_get_state(config=config)

        state = agent_app.get_state(config=config)
        return await state if inspect.isawaitable(state) else state

    @staticmethod
    def _normalize_graph_stream_chunk(chunk) -> tuple[str, Any]:
        if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] in {"updates", "custom"}:
            return chunk
        return "updates", chunk

    @staticmethod
    def _text_delta_payload(payload: Any, *, run_id: str) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or payload.get("event_type") != "text_delta":
            return None
        delta = str(payload.get("delta") or "")
        if not delta:
            return None
        return {"run_id": payload.get("run_id") or run_id, "delta": delta}

    @staticmethod
    def _default_run_config() -> dict[str, Any]:
        return {
            "temperature": app_settings.TEMPERATURE,
            "max_steps": app_settings.AGENT_MAX_STEPS,
            "max_tokens": app_settings.LLM_MAX_TOKENS,
            "tool_timeout_seconds": app_settings.EXTERNAL_CONNECTOR_TIMEOUT_SECONDS,
            "context_window_tokens": app_settings.CONTEXT_WINDOW_TOKENS,
            "context_compaction_trigger_ratio": app_settings.CONTEXT_COMPACTION_TRIGGER_RATIO,
            "context_compaction_target_ratio": app_settings.CONTEXT_COMPACTION_TARGET_RATIO,
        }

    @classmethod
    def _run_config(cls, run_record) -> dict[str, Any]:
        raw = getattr(run_record, "run_config_json", None)
        if not isinstance(raw, dict):
            raw = {}
        return {**cls._default_run_config(), **raw}

    @staticmethod
    def _int_config(config: dict[str, Any], key: str, default: int) -> int:
        try:
            value = int(config.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @staticmethod
    def _terminal_error_message(run_record) -> str | None:
        if run_record is None:
            return None
        status = getattr(run_record, "status", None)
        if status == RunStatus.CANCELLED.value:
            return "Run was cancelled before the agent finished."
        if status == RunStatus.TIMED_OUT.value:
            return "Run timed out before the agent finished."
        return None

    @classmethod
    def _mark_run_failed(
        cls,
        db: Session,
        run_id: uuid.UUID,
        *,
        error_message: str,
        source: str,
    ) -> str:
        updated_run = RunRepository.update_run_status(
            db,
            run_id,
            status=RunStatus.FAILED.value,
            error_msg=error_message,
            commit=False,
        )
        terminal_error = cls._terminal_error_message(updated_run)
        if terminal_error:
            db.rollback()
            return terminal_error
        MessageRepository.mark_pending_interventions_undelivered(
            db,
            run_id,
            reason=error_message,
            commit=False,
        )
        RunStepRepository.create_error_step(
            db=db,
            run_id=run_id,
            summary=error_message,
            metadata={"source": source},
            commit=False,
        )
        db.commit()
        return error_message

    @staticmethod
    def _serialize_steps(db: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return serialize_timeline_steps(db, run_id)

    @staticmethod
    def _build_run_suspended_event(
        run_id: uuid.UUID,
        approval: Any,
        tool_call: Any | None,
    ) -> dict[str, Any]:
        return {
            "run_id": str(run_id),
            "approval_request_id": str(approval.id),
            "tool_name": tool_call.skill_name if tool_call else None,
            "tool_input": tool_call.arguments_json if tool_call else None,
            "risk_level": tool_call.risk_level if tool_call else None,
        }
