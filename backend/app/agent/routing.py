from __future__ import annotations

from typing import Literal

from langchain_core.runnables import RunnableConfig

from app.agent.builtin_tools import BUILTIN_TOOL_NAMES, classify_builtin_risk
from app.agent.state import AgentState


def decide_tool_route(
    *,
    risk_levels: list[str],
    all_skills_ready: bool = True,
) -> Literal["execute_tools", "suspend_for_human", "fail"]:
    if not all_skills_ready or "prohibited" in risk_levels:
        return "fail"
    if "dangerous_action" in risk_levels:
        return "suspend_for_human"
    return "execute_tools"


def reliability_router(
    state: AgentState,
    config: RunnableConfig,
) -> Literal["execute_tools", "suspend_for_human", "fail", "end"]:
    """Route tool calls without mutating persistent state from the router."""
    if state.execution_error:
        return "end"
    if state.current_step_index >= state.max_steps:
        return "fail"

    response = state.latest_response
    if not response or not response.tool_calls:
        return "end"

    risk_levels: list[str] = []
    all_skills_ready = True
    for tool_call in response.tool_calls:
        if tool_call.name not in BUILTIN_TOOL_NAMES:
            all_skills_ready = False
            continue
        risk_levels.append(classify_builtin_risk(tool_call.name, tool_call.arguments))

    return decide_tool_route(
        risk_levels=risk_levels,
        all_skills_ready=all_skills_ready,
    )


def route_after_tool_execution(state: AgentState) -> Literal["call_llm_gateway", "end"]:
    return "end" if state.execution_error else "call_llm_gateway"
