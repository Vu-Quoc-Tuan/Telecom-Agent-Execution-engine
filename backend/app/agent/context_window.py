from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages.utils import count_tokens_approximately, trim_messages

from app.agent.safety import AgentSafetyGuard
from app.llm.langchain_messages import to_langchain_messages
from app.llm.langchain_model import to_langchain_tools
from app.llm.schemas import LLMMessage, LLMToolDefinition, MessageRole


@dataclass(frozen=True)
class ContextWindowPlan:
    messages: list[LLMMessage]
    original_tokens: int
    compacted_tokens: int
    threshold_tokens: int
    was_compacted: bool


def estimate_context_tokens(
    messages: Sequence[LLMMessage],
    *,
    system_prompt: str | None = None,
    tools: Sequence[LLMToolDefinition] | None = None,
) -> int:
    return count_tokens_approximately(
        to_langchain_messages(messages, system_prompt),
        tools=to_langchain_tools(tools, supports_strict=False),
    )


def _trim_recent_messages(
    messages: Sequence[LLMMessage],
    *,
    max_tokens: int,
) -> list[LLMMessage]:
    trimmed = trim_messages(
        to_langchain_messages(messages),
        max_tokens=max_tokens,
        token_counter="approximate",
        strategy="last",
        allow_partial=False,
        start_on="human",
    )
    if trimmed:
        return list(messages[-len(trimmed) :])
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is MessageRole.USER:
            return list(messages[index:])
    return []


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
    original_tokens = estimate_context_tokens(
        messages,
        system_prompt=system_prompt,
        tools=tools,
    )
    if original_tokens <= threshold_tokens or len(messages) < 3:
        return ContextWindowPlan(
            messages=list(messages),
            original_tokens=original_tokens,
            compacted_tokens=original_tokens,
            threshold_tokens=threshold_tokens,
            was_compacted=False,
        )

    target_tokens = max(1, int(context_window_tokens * target_ratio))
    fixed_tokens = estimate_context_tokens(
        [],
        system_prompt=system_prompt,
        tools=tools,
    )
    retained = _trim_recent_messages(
        messages,
        max_tokens=max(1, target_tokens - fixed_tokens),
    )
    if not retained:
        return ContextWindowPlan(
            messages=list(messages),
            original_tokens=original_tokens,
            compacted_tokens=original_tokens,
            threshold_tokens=threshold_tokens,
            was_compacted=False,
        )

    cutoff = len(messages) - len(retained)
    old_messages = list(messages[:cutoff])
    if not old_messages:
        return ContextWindowPlan(
            messages=list(messages),
            original_tokens=original_tokens,
            compacted_tokens=original_tokens,
            threshold_tokens=threshold_tokens,
            was_compacted=False,
        )

    compacted_messages = [
        LLMMessage(
            role=MessageRole.SYSTEM,
            content=_summarize_messages(
                old_messages,
                max_characters=summary_max_characters,
            ),
        ),
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
