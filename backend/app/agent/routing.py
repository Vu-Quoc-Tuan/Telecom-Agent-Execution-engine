from __future__ import annotations

import uuid
from typing import Literal

from langchain_core.runnables import RunnableConfig

from app.agent.state import AgentState
from app.agent.tool_batch_planner import build_tool_batch_plan
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
    settings = config["configurable"].get("settings")
    plan = build_tool_batch_plan(
        db=db,
        tool_calls=response.tool_calls,
        settings=settings,
        tolerate_environment_errors=True,
    )
    return plan.route


def route_after_tool_execution(state: AgentState) -> Literal["call_llm_gateway", "end"]:
    return "end" if state.execution_error else "call_llm_gateway"
