from __future__ import annotations

import uuid
from typing import Literal

from langchain_core.runnables import RunnableConfig

from app.agent.builtin_tools import (
    BUILTIN_TOOL_NAMES,
    build_builtin_tool_definitions,
    classify_builtin_risk,
)
from app.agent.state import AgentState
from app.agent.tool_validation import validate_tool_call_arguments
from app.common.exceptions import TelecomAgentException
from app.database.repositories.messages import MessageRepository


def decide_tool_route(
    *,
    risk_levels: list[str],
    all_skills_ready: bool = True,
) -> Literal["execute_tools", "suspend_for_human", "fail"]:
    if not all_skills_ready or "prohibited" in risk_levels:
        return "fail"
    # Giữ "dangerous_action" để tương thích nhãn cũ truyền từ ngoài vào.
    if "dangerous_action" in risk_levels or "require_approval" in risk_levels:
        return "suspend_for_human"
    return "execute_tools"


def reliability_router(
    state: AgentState,
    config: RunnableConfig,
) -> Literal["execute_tools", "suspend_for_human", "fail", "end", "call_llm_gateway"]:
    """Route tool calls without mutating persistent state from the router."""
    if state.execution_error:
        return "end"

    response = state.latest_response

    # Không có tool call: hoặc đã ra câu trả lời cuối, hoặc cần quay lại LLM nếu có
    # can thiệp của operator được xếp hàng trong lúc run đang chạy.
    if not response or not response.tool_calls:
        db = config["configurable"]["db"]
        pending = MessageRepository.list_pending_interventions(db, uuid.UUID(state.run_id))
        if pending:
            return "call_llm_gateway"
        return "end"

    if state.current_step_index >= state.max_steps:
        return "fail"

    db = config["configurable"]["db"]
    from app.database.repositories.skills import SkillRepository

    try:
        ready_skills = SkillRepository.list_ready_skills(db)
    except Exception:
        ready_skills = []
    ready_skill_names = {skill.name for skill in ready_skills}

    settings = config["configurable"].get("settings")
    try:
        from app.sandbox.docker_executor import sandbox_available

        is_sandbox_available = sandbox_available(settings)
    except Exception:
        is_sandbox_available = False
    tool_catalog = build_builtin_tool_definitions(
        ready_skills,
        sandbox_available=is_sandbox_available,
        settings=settings,
    )

    risk_levels: list[str] = []
    all_skills_ready = True
    has_invalid_call = False
    for tool_call in response.tool_calls:
        if tool_call.name not in BUILTIN_TOOL_NAMES:
            if tool_call.name in ready_skill_names:
                has_invalid_call = True
            else:
                all_skills_ready = False
            continue
        try:
            validate_tool_call_arguments(
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                tools=tool_catalog,
            )
            risk_levels.append(classify_builtin_risk(tool_call.name, tool_call.arguments))
        except TelecomAgentException:
            # Tool call không hợp lệ/không khả dụng: không crash router.
            has_invalid_call = True

    # Batch có tool call không hợp lệ → ưu tiên đẩy sang execute_tools để node tự
    # validate và phản hồi lỗi lại cho LLM (feedback path), trước khi xét suspend.
    if has_invalid_call and all_skills_ready:
        return "execute_tools"

    return decide_tool_route(
        risk_levels=risk_levels,
        all_skills_ready=all_skills_ready,
    )


def route_after_tool_execution(state: AgentState) -> Literal["call_llm_gateway", "end"]:
    return "end" if state.execution_error else "call_llm_gateway"
