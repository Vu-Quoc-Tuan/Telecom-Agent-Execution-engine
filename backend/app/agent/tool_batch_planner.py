from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

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


@dataclass(frozen=True)
class ToolPlanItem:
    index: int
    tool_call: NormalizedToolCall
    risk_level: str | None = None
    error_message: str | None = None
    unavailable: bool = False

    @property
    def is_valid(self) -> bool:
        return self.error_message is None

    @property
    def requires_approval(self) -> bool:
        return self.risk_level == ExecutionMode.REQUIRE_APPROVAL.value


@dataclass(frozen=True)
class ToolBatchPlan:
    route: ToolRoute
    items: list[ToolPlanItem]
    tool_catalog: Sequence[LLMToolDefinition]
    ready_skill_names: set[str]

    @property
    def risk_levels(self) -> list[str]:
        return [item.risk_level for item in self.items if item.risk_level is not None]

    @property
    def has_invalid_call(self) -> bool:
        return any(item.error_message is not None and not item.unavailable for item in self.items)

    @property
    def all_tools_available(self) -> bool:
        return not any(item.unavailable for item in self.items)


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
    has_invalid_call = any(item.error_message is not None and not item.unavailable for item in items)

    if not all_tools_available or "prohibited" in risk_levels:
        return "fail"
    if has_invalid_call:
        return "execute_tools"
    if "dangerous_action" in risk_levels or ExecutionMode.REQUIRE_APPROVAL.value in risk_levels:
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
        tool_catalog=tool_catalog,
        ready_skill_names=ready_skill_names,
    )


def build_tool_batch_plan(
    *,
    db: Any,
    tool_calls: Sequence[NormalizedToolCall],
    settings: Any,
    tolerate_environment_errors: bool = False,
) -> ToolBatchPlan:
    ready_skills = _ready_skills(db, tolerate_errors=tolerate_environment_errors)
    return plan_tool_batch(
        tool_calls=tool_calls,
        ready_skills=ready_skills,
        sandbox_available=_sandbox_available(
            settings,
            tolerate_errors=tolerate_environment_errors,
        ),
        settings=settings,
    )
