from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.agent.safety import AgentSafetyGuard
from app.llm.schemas import LLMMessage, LLMToolDefinition, MessageRole


@dataclass(frozen=True)
class ContextWindowPlan:
    messages: list[LLMMessage]
    original_tokens: int
    compacted_tokens: int
    threshold_tokens: int
    was_compacted: bool


def estimate_text_tokens(value: str | None) -> int:
    if not value:
        return 0
    return max(1, math.ceil(len(value) / 4))


def estimate_tool_tokens(tools: Sequence[LLMToolDefinition] | None) -> int:
    if not tools:
        return 0
    total = 0
    for tool in tools:
        total += estimate_text_tokens(tool.name)
        total += estimate_text_tokens(tool.description)
        total += estimate_text_tokens(str(tool.input_schema))
        total += 12
    return total


def estimate_message_tokens(message: LLMMessage) -> int:
    total = 8 + estimate_text_tokens(message.content)
    total += estimate_text_tokens(message.name)
    total += estimate_text_tokens(message.tool_call_id)
    for tool_call in message.tool_calls:
        total += estimate_text_tokens(tool_call.id)
        total += estimate_text_tokens(tool_call.name)
        total += estimate_text_tokens(str(tool_call.arguments))
        total += 16
    return total


def estimate_context_tokens(
    messages: Sequence[LLMMessage],
    *,
    system_prompt: str | None = None,
    tools: Sequence[LLMToolDefinition] | None = None,
) -> int:
    return (
        estimate_text_tokens(system_prompt)
        + estimate_tool_tokens(tools)
        + sum(estimate_message_tokens(message) for message in messages)
    )


def compact_messages_if_needed(
    messages: Sequence[LLMMessage],
    *,
    system_prompt: str | None = None,
    tools: Sequence[LLMToolDefinition] | None = None,
    context_window_tokens: int,
    trigger_ratio: float,
    target_ratio: float,
    summary_max_characters: int = 6000,
) -> ContextWindowPlan:
    threshold_tokens = max(1, int(context_window_tokens * trigger_ratio))
    original_tokens = estimate_context_tokens(messages, system_prompt=system_prompt, tools=tools)
    if original_tokens <= threshold_tokens or len(messages) < 3:
        return ContextWindowPlan(
            messages=list(messages),
            original_tokens=original_tokens,
            compacted_tokens=original_tokens,
            threshold_tokens=threshold_tokens,
            was_compacted=False,
        )

    target_tokens = max(1, int(context_window_tokens * target_ratio))
    fixed_tokens = estimate_text_tokens(system_prompt) + estimate_tool_tokens(tools)
    retained: list[LLMMessage] = []
    retained_tokens = fixed_tokens

    for message in reversed(messages):
        message_tokens = estimate_message_tokens(message)
        if retained and retained_tokens + message_tokens > target_tokens:
            break
        retained.insert(0, message)
        retained_tokens += message_tokens

    if not retained:
        retained = [messages[-1]]

    cutoff = len(messages) - len(retained)
    while cutoff > 0 and retained and retained[0].role is MessageRole.TOOL:
        cutoff -= 1
        retained.insert(0, messages[cutoff])

    old_messages = list(messages[:cutoff])
    if not old_messages:
        return ContextWindowPlan(
            messages=list(messages),
            original_tokens=original_tokens,
            compacted_tokens=original_tokens,
            threshold_tokens=threshold_tokens,
            was_compacted=False,
        )

    summary = _summarize_messages(old_messages, max_characters=summary_max_characters)
    compacted_messages = [
        LLMMessage(role=MessageRole.SYSTEM, content=summary),
        *retained,
    ]
    compacted_tokens = estimate_context_tokens(
        compacted_messages,
        system_prompt=system_prompt,
        tools=tools,
    )
    return ContextWindowPlan(
        messages=compacted_messages,
        original_tokens=original_tokens,
        compacted_tokens=compacted_tokens,
        threshold_tokens=threshold_tokens,
        was_compacted=True,
    )


def _summarize_messages(messages: Sequence[LLMMessage], *, max_characters: int) -> str:
    lines = [
        "[AUTO-COMPACTED CONTEXT]",
        "Older conversation/tool outputs were compressed to protect the LLM context window.",
    ]
    for index, message in enumerate(messages, start=1):
        prefix = f"{index}. {message.role.value}"
        content = _preview(message.content)
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            tools = ", ".join(
                f"{tool_call.name}({_preview(str(tool_call.arguments), limit=180)})"
                for tool_call in message.tool_calls
            )
            lines.append(f"{prefix}: requested tool(s): {tools}; said: {content}")
            continue
        if message.role is MessageRole.TOOL:
            lines.append(
                f"{prefix}: tool_call_id={message.tool_call_id}; "
                f"chars={len(message.content or '')}; output={content}"
            )
            continue
        lines.append(f"{prefix}: {content}")

    summary = "\n".join(lines)
    if len(summary) <= max_characters:
        return summary
    return (
        summary[:max_characters]
        + "\n... [AUTO-COMPACTION SUMMARY TRUNCATED TO FIT CONTEXT BUDGET] ..."
    )


def _preview(value: str | None, *, limit: int = 500) -> str:
    if not value:
        return ""
    sanitized = AgentSafetyGuard.sanitize_input_prompt(value)
    normalized = " ".join(sanitized.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
