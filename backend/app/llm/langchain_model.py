from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from pydantic import ConfigDict

from app.llm.base import LLMAdapterConfig
from app.llm.schemas import (
    LLMRequestOptions,
    LLMToolDefinition,
    ToolChoiceMode,
)

_RUN_CONFIG_KEYS = {"callbacks", "tags", "metadata", "run_name", "run_id"}


class LangChainChatConfig(LLMAdapterConfig):
    """Configuration shared by the OpenAI and Anthropic LangChain wrappers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai", "anthropic"] = "openai"
    supports_tool_strict: bool = True
    stream_usage: bool = True


def build_chat_model(
    config: LangChainChatConfig,
    options: LLMRequestOptions | None = None,
):
    model_name = options.model if options and options.model else config.model
    timeout = (
        options.timeout_seconds
        if options and options.timeout_seconds is not None
        else config.timeout_seconds
    )
    max_tokens = (
        options.max_tokens
        if options and options.max_tokens is not None
        else config.default_max_tokens
    )

    model_kwargs: dict[str, Any] = {
        **config.default_params,
        "model": model_name,
        "api_key": config.api_key.get_secret_value(),
        "timeout": timeout,
        "max_retries": config.max_retries,
        "stream_usage": config.stream_usage,
    }
    if options and options.temperature is not None:
        model_kwargs["temperature"] = options.temperature
    if options and options.top_p is not None:
        model_kwargs["top_p"] = options.top_p
    if config.base_url:
        model_kwargs["base_url"] = config.base_url

    if config.provider == "openai":
        model_kwargs["max_completion_tokens"] = max_tokens
        return ChatOpenAI(**model_kwargs)

    model_kwargs["max_tokens"] = max_tokens
    return ChatAnthropic(**model_kwargs)


def select_chat_model(
    base_model,
    config: LangChainChatConfig,
    options: LLMRequestOptions | None,
):
    if not options:
        return base_model
    rebuild = any(
        value is not None
        for value in (
            options.model,
            options.temperature,
            options.top_p,
            options.max_tokens,
            options.timeout_seconds,
        )
    )
    return build_chat_model(config, options) if rebuild else base_model


def to_langchain_tools(
    tools: Sequence[LLMToolDefinition] | None,
    *,
    supports_strict: bool,
) -> list[dict[str, Any]]:
    return [
        convert_to_openai_tool(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
            strict=True if supports_strict and tool.strict else None,
        )
        for tool in tools or ()
    ]


def bind_tools_if_needed(
    model,
    config: LangChainChatConfig,
    tools: Sequence[LLMToolDefinition] | None,
    options: LLMRequestOptions | None,
):
    if options and options.tool_choice and options.tool_choice.mode is ToolChoiceMode.NONE:
        return model

    tool_payloads = to_langchain_tools(
        tools,
        supports_strict=config.supports_tool_strict,
    )
    if not tool_payloads:
        return model

    bind_kwargs: dict[str, Any] = {}
    if options and options.tool_choice and options.tool_choice.mode is ToolChoiceMode.SPECIFIC:
        bind_kwargs["tool_choice"] = options.tool_choice.tool_name
    if options and options.parallel_tool_calls is not None:
        bind_kwargs["parallel_tool_calls"] = options.parallel_tool_calls
    return model.bind_tools(tool_payloads, **bind_kwargs)


def runnable_config(provider_kwargs: dict[str, Any]) -> RunnableConfig | None:
    config = {
        key: value
        for key, value in provider_kwargs.items()
        if key in _RUN_CONFIG_KEYS and value is not None
    }
    return config or None


def model_call_kwargs(
    options: LLMRequestOptions | None,
    provider_kwargs: dict[str, Any],
) -> dict[str, Any]:
    kwargs = {key: value for key, value in provider_kwargs.items() if key not in _RUN_CONFIG_KEYS}
    if options and options.stop is not None:
        kwargs["stop"] = options.stop
    return kwargs
