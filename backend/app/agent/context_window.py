from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages.utils import count_tokens_approximately, trim_messages

from app.agent.safety import AgentSafetyGuard
from app.llm.langchain_messages import to_langchain_messages
from app.llm.langchain_model import to_langchain_tools
from app.llm.schemas import (
    LLMMessage,
    LLMRequestOptions,
    LLMToolDefinition,
    MessageRole,
    ToolChoice,
    ToolChoiceMode,
)
from app.observability.logging import app_logger
from app.observability.redaction import DataRedactor

if TYPE_CHECKING:
    from app.llm.gateway import LLMGateway


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


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return AgentSafetyGuard.sanitize_input_prompt(DataRedactor.redact_text(value))


def _sanitize_messages(messages: Sequence[LLMMessage]) -> list[LLMMessage]:
    return [
        message.model_copy(
            update={
                "content": _sanitize_text(message.content),
                "tool_calls": [
                    tool_call.model_copy(
                        update={"arguments": DataRedactor.redact_dict(tool_call.arguments)}
                    )
                    for tool_call in message.tool_calls
                ],
            }
        )
        for message in messages
    ]


def _summarize_messages_deterministically(
    messages: Sequence[LLMMessage], *, max_characters: int
) -> str:
    lines = ["Older conversation/tool outputs were compressed to protect the context window."]
    for index, message in enumerate(messages, start=1):
        prefix = f"{index}. {message.role.value}"
        content = _preview(message.content)
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            tools = ", ".join(
                f"{tool_call.name}({_preview(str(tool_call.arguments), limit=180)})"
                for tool_call in message.tool_calls
            )
            lines.append(f"{prefix}: requested tool(s): {tools}; said: {content}")
        elif message.role is MessageRole.TOOL:
            lines.append(
                f"{prefix}: tool_call_id={message.tool_call_id}; "
                f"chars={len(message.content or '')}; output={content}"
            )
        else:
            lines.append(f"{prefix}: {content}")

    summary = "\n".join(lines)
    if len(summary) <= max_characters:
        return summary
    return summary[:max_characters] + "\n... [DETERMINISTIC SUMMARY TRUNCATED] ..."


def _preview(value: str | None, *, limit: int = 500) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _fit_compacted_messages(
    summary: str,
    retained: Sequence[LLMMessage],
    *,
    system_prompt: str | None,
    tools: Sequence[LLMToolDefinition] | None,
    max_tokens: int,
) -> tuple[list[LLMMessage], int] | None:
    def build(summary_text: str) -> list[LLMMessage]:
        return [
            LLMMessage(
                role=MessageRole.SYSTEM,
                content=f"[AUTO-COMPACTED CONTEXT]\n{summary_text}",
            ),
            *retained,
        ]

    full_messages = build(summary)
    full_tokens = estimate_context_tokens(
        full_messages,
        system_prompt=system_prompt,
        tools=tools,
    )
    if full_tokens <= max_tokens:
        return full_messages, full_tokens

    empty_messages = build("")
    empty_tokens = estimate_context_tokens(
        empty_messages,
        system_prompt=system_prompt,
        tools=tools,
    )
    if empty_tokens > max_tokens:
        return None

    low = 0
    high = len(summary)
    best_messages = empty_messages
    best_tokens = empty_tokens
    while low <= high:
        midpoint = (low + high) // 2
        suffix = (
            "\n... [SUMMARY TRUNCATED TO FIT CONTEXT BUDGET] ..."
            if midpoint < len(summary)
            else ""
        )
        candidate_messages = build(summary[:midpoint] + suffix)
        candidate_tokens = estimate_context_tokens(
            candidate_messages,
            system_prompt=system_prompt,
            tools=tools,
        )
        if candidate_tokens <= max_tokens:
            best_messages = candidate_messages
            best_tokens = candidate_tokens
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best_messages, best_tokens


async def _summarize_messages_with_llm(
    messages: Sequence[LLMMessage],
    *,
    llm_gateway: LLMGateway,
    compaction_prompt: str,
    provider: str | None,
    max_tokens: int,
    timeout_seconds: float,
) -> str:
    response = await llm_gateway.invoke(
        messages,
        provider=provider,
        system_prompt=compaction_prompt,
        tools=[],
        options=LLMRequestOptions(
            temperature=0,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            tool_choice=ToolChoice(mode=ToolChoiceMode.NONE),
            parallel_tool_calls=False,
        ),
    )
    summary = (response.content or "").strip()
    if not summary:
        raise RuntimeError("Context compactor returned an empty summary.")
    return _sanitize_text(summary) or ""


async def compact_messages_if_needed(
    messages: Sequence[LLMMessage],
    *,
    llm_gateway: LLMGateway,
    compaction_prompt: str | Callable[[], str],
    provider: str | None = None,
    system_prompt: str | None = None,
    tools: Sequence[LLMToolDefinition] | None = None,
    context_window_tokens: int,
    trigger_ratio: float,
    target_ratio: float,
    summary_max_tokens: int = 1200,
    summary_timeout_seconds: float = 30.0,
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

    sanitized_old_messages = _sanitize_messages(old_messages)
    try:
        resolved_prompt = compaction_prompt() if callable(compaction_prompt) else compaction_prompt
        summary = await _summarize_messages_with_llm(
            sanitized_old_messages,
            llm_gateway=llm_gateway,
            compaction_prompt=resolved_prompt,
            provider=provider,
            max_tokens=summary_max_tokens,
            timeout_seconds=summary_timeout_seconds,
        )
    except Exception as exc:
        app_logger.warning(
            "LLM context compaction failed; using deterministic fallback (%s).",
            type(exc).__name__,
        )
        summary = _summarize_messages_deterministically(
            sanitized_old_messages,
            max_characters=max(500, summary_max_tokens * 4),
        )
    fitted = _fit_compacted_messages(
        summary,
        retained,
        system_prompt=system_prompt,
        tools=tools,
        max_tokens=min(target_tokens, original_tokens - 1),
    )
    if fitted is None:
        fitted = _fit_compacted_messages(
            summary,
            retained,
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=original_tokens - 1,
        )
    if fitted is None:
        compacted_messages = list(retained)
        compacted_tokens = estimate_context_tokens(
            compacted_messages,
            system_prompt=system_prompt,
            tools=tools,
        )
    else:
        compacted_messages, compacted_tokens = fitted
    return ContextWindowPlan(
        messages=compacted_messages,
        original_tokens=original_tokens,
        compacted_tokens=compacted_tokens,
        threshold_tokens=threshold_tokens,
        was_compacted=True,
    )
