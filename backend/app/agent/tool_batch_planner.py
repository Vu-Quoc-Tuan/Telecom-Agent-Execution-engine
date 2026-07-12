from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.builtin_tools import (
    BUILTIN_TOOL_NAMES,
    build_builtin_tool_definitions,
    classify_builtin_risk,
)
from app.agent.tool_validation import validate_tool_call_arguments
from app.common.enums import ExecutionMode
from app.common.exceptions import TelecomAgentException
from app.llm.schemas import LLMToolDefinition, NormalizedToolCall

ToolRoute = Literal["execute_tools", "suspend_for_human", "fail"]


class ToolPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int
    tool_call: NormalizedToolCall
    risk_level: str | None = None
    error_message: str | None = None
    unavailable: bool = False


class ToolBatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: ToolRoute
    items: list[ToolPlanItem]
    # Kept for backward-compat with serialized checkpoints; no longer populated.
    tool_catalog: list[LLMToolDefinition] = Field(default_factory=list)
    ready_skill_names: list[str] = Field(default_factory=list)


def tool_batch_plan_matches(
    plan: ToolBatchPlan,
    tool_calls: Sequence[NormalizedToolCall],
) -> bool:
    planned_calls = [
        (item.tool_call.id, item.tool_call.name, item.tool_call.arguments) for item in plan.items
    ]
    requested_calls = [
        (tool_call.id, tool_call.name, tool_call.arguments) for tool_call in tool_calls
    ]
    return planned_calls == requested_calls


def _sandbox_available(settings: Any, *, tolerate_errors: bool) -> bool:
    try:
        from app.sandbox.docker_executor import sandbox_available

        return sandbox_available(settings)
    except Exception:
        if tolerate_errors:
            return False
        raise


def _ready_skills(db: Any, *, tolerate_errors: bool):
    from app.database.repositories.skills import SkillRepository

    try:
        return SkillRepository.list_ready_skills(db)
    except Exception:
        if tolerate_errors:
            return []
        raise


def _skill_name_error(tool_name: str) -> str:
    return (
        f"Tool '{tool_name}' is a skill name, not a built-in tool. "
        f"To use/execute this skill, you must first call the 'load_skill' tool "
        f"with argument skill_name='{tool_name}' to load its documentation/files."
    )


def _plan_route(items: list[ToolPlanItem]) -> ToolRoute:
    all_tools_available = not any(item.unavailable for item in items)
    risk_levels = [item.risk_level for item in items if item.risk_level is not None]
    has_invalid_call = any(
        item.error_message is not None and not item.unavailable for item in items
    )

    if not all_tools_available or "prohibited" in risk_levels:
        return "fail"
    if has_invalid_call:
        return "execute_tools"
    if ExecutionMode.REQUIRE_APPROVAL.value in risk_levels:
        return "suspend_for_human"
    return "execute_tools"


def plan_tool_batch(
    *,
    tool_calls: Sequence[NormalizedToolCall],
    ready_skills: Sequence[Any],
    sandbox_available: bool,
    settings: Any,
) -> ToolBatchPlan:
    ready_skill_names = {skill.name for skill in ready_skills}
    tool_catalog = build_builtin_tool_definitions(
        ready_skills,
        sandbox_available=sandbox_available,
        settings=settings,
    )
    items: list[ToolPlanItem] = []

    for index, tool_call in enumerate(tool_calls):
        if tool_call.name not in BUILTIN_TOOL_NAMES:
            if tool_call.name in ready_skill_names:
                items.append(
                    ToolPlanItem(
                        index=index,
                        tool_call=tool_call,
                        error_message=_skill_name_error(tool_call.name),
                    )
                )
            else:
                items.append(
                    ToolPlanItem(
                        index=index,
                        tool_call=tool_call,
                        error_message=f"Tool '{tool_call.name}' is not available.",
                        unavailable=True,
                    )
                )
            continue

        try:
            validate_tool_call_arguments(
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                tools=tool_catalog,
            )
            risk_level = classify_builtin_risk(tool_call.name, tool_call.arguments)
        except TelecomAgentException as exc:
            items.append(ToolPlanItem(index=index, tool_call=tool_call, error_message=exc.message))
            continue

        items.append(ToolPlanItem(index=index, tool_call=tool_call, risk_level=risk_level))

    return ToolBatchPlan(
        route=_plan_route(items),
        items=items,
    )


def build_tool_batch_plan(
    *,
    db: Any,
    tool_calls: Sequence[NormalizedToolCall],
    settings: Any,
    tolerate_environment_errors: bool = False,
    selected_skill: str | None = None,
) -> ToolBatchPlan:
    ready_skills = _ready_skills(db, tolerate_errors=tolerate_environment_errors)
    if selected_skill:
        ready_skills = [s for s in ready_skills if s.name == selected_skill]
    return plan_tool_batch(
        tool_calls=tool_calls,
        ready_skills=ready_skills,
        sandbox_available=_sandbox_available(
            settings,
            tolerate_errors=tolerate_environment_errors,
        ),
        settings=settings,
    )
