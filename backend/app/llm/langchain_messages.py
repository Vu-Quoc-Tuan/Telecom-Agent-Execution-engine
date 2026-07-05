from collections.abc import Sequence

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.llm.schemas import (
    FinishReason,
    LLMMessage,
    LLMResponse,
    MessageRole,
    NormalizedToolCall,
    TokenUsage,
)


def to_langchain_messages(
    messages: Sequence[LLMMessage],
    system_prompt: str | None = None,
) -> list[BaseMessage]:
    output: list[BaseMessage] = []
    if system_prompt:
        output.append(SystemMessage(content=system_prompt))

    for message in messages:
        if message.role is MessageRole.SYSTEM:
            if message.content:
                output.append(SystemMessage(content=message.content))
        elif message.role is MessageRole.USER:
            kwargs = {"name": message.name} if message.name else {}
            output.append(HumanMessage(content=message.content or "", **kwargs))
        elif message.role is MessageRole.ASSISTANT:
            output.append(
                AIMessage(
                    content="" if message.tool_calls else (message.content or ""),
                    tool_calls=[
                        {
                            "name": tool_call.name,
                            "args": tool_call.arguments,
                            "id": tool_call.id,
                        }
                        for tool_call in message.tool_calls
                    ],
                )
            )
        elif message.role is MessageRole.TOOL:
            output.append(
                ToolMessage(
                    content=message.content or "",
                    tool_call_id=message.tool_call_id or "",
                    status="error" if message.tool_is_error else "success",
                )
            )
    return output


def _tool_calls_from_message(
    message: AIMessage | AIMessageChunk,
) -> list[NormalizedToolCall]:
    normalized: list[NormalizedToolCall] = []
    for index, tool_call in enumerate(message.tool_calls):
        arguments = tool_call.get("args") or {}
        if not isinstance(arguments, dict):
            arguments = {"input": arguments}
        normalized.append(
            NormalizedToolCall(
                id=str(tool_call.get("id") or f"call_{index}"),
                name=str(tool_call.get("name") or ""),
                arguments=arguments,
            )
        )
    return normalized


def _token_usage(message: AIMessage | AIMessageChunk) -> TokenUsage:
    usage = message.usage_metadata or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        details={
            key: value
            for key, value in usage.items()
            if key not in {"input_tokens", "output_tokens", "total_tokens"}
        },
    )


def finish_reason(
    message: AIMessage | AIMessageChunk,
    tool_calls: list[NormalizedToolCall],
) -> FinishReason:
    if tool_calls:
        return FinishReason.TOOL
    raw = message.response_metadata.get("finish_reason") or message.response_metadata.get(
        "stop_reason"
    )
    if raw in {"stop", "end_turn"}:
        return FinishReason.STOP
    if raw in {"length", "max_tokens"}:
        return FinishReason.LENGTH
    if raw in {"tool_calls", "tool_use"}:
        return FinishReason.TOOL
    if raw in {"content_filter", "safety"}:
        return FinishReason.CONTENT_FILTER
    return FinishReason.UNKNOWN


def model_name(message: AIMessage | AIMessageChunk, fallback: str) -> str:
    return str(
        message.response_metadata.get("model_name")
        or message.response_metadata.get("model")
        or fallback
    )


def normalize_response(
    message: AIMessage | AIMessageChunk,
    *,
    provider: str,
    fallback_model: str,
) -> LLMResponse:
    tool_calls = _tool_calls_from_message(message)
    return LLMResponse(
        content=message.text or None,
        provider=provider,
        model=model_name(message, fallback_model),
        response_id=message.id,
        tool_calls=tool_calls,
        usage=_token_usage(message),
        finish_reason=finish_reason(message, tool_calls),
    )
