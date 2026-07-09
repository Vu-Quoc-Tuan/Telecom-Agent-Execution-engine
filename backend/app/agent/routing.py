from __future__ import annotations

import uuid
from typing import Literal

from langchain_core.runnables import RunnableConfig

from app.agent.nodes import _selected_skill_from_config
from app.agent.state import AgentState
from app.agent.tool_batch_planner import build_tool_batch_plan, tool_batch_plan_matches
from app.database.repositories.messages import MessageRepository


def reliability_router(
    state: AgentState,
    config: RunnableConfig,
) -> Literal["execute_tools", "suspend_for_human", "fail", "end", "call_llm_gateway"]:
    """Route tool calls without mutating persistent state from the router."""
    if state.execution_error:
        return "end"

    response = state.latest_response

    if not response or not response.tool_calls:
        db = config["configurable"]["db"]
        pending = MessageRepository.list_pending_interventions(db, uuid.UUID(state.run_id))
        if pending:
            return "call_llm_gateway"
        return "end"

    if state.current_step_index >= state.max_steps:
        return "fail"

    plan = state.tool_batch_plan
    if plan is not None and tool_batch_plan_matches(plan, response.tool_calls):
        return plan.route

    db = config["configurable"]["db"]
    settings = config["configurable"].get("settings")
    selected_skill = _selected_skill_from_config(config)
    plan = build_tool_batch_plan(
        db=db,
        tool_calls=response.tool_calls,
        settings=settings,
        tolerate_environment_errors=True,
        selected_skill=selected_skill,
    )
    return plan.route


def route_after_tool_execution(state: AgentState) -> Literal["call_llm_gateway", "end"]:
    return "end" if state.execution_error else "call_llm_gateway"
