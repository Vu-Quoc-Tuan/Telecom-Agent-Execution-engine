import inspect
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from pydantic import ConfigDict

from app.llm.base import BaseLLMAdapter, LLMAdapterConfig
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMPermissionError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseFormatError,
    LLMTimeoutError,
    LLMUnsupportedFeatureError,
)
from app.llm.schemas import (
    FinishReason,
    LLMMessage,
    LLMRequestOptions,
    LLMResponse,
    LLMStreamChunk,
    LLMToolDefinition,
    MessageRole,
    NormalizedToolCall,
    StreamEventType,
    TokenUsage,
    ToolCallDelta,
    ToolChoiceMode,
)


class AnthropicConfig(LLMAdapterConfig):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "anthropic"


class AnthropicAdapter(BaseLLMAdapter):
    _RESERVED_REQUEST_KEYS = {
        "model",
        "messages",
        "system",
        "tools",
        "tool_choice",
        "stream",
        "max_tokens",
    }

    def __init__(self, config: AnthropicConfig) -> None:
        super().__init__(config)
        self.config = config

        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key.get_secret_value(),
            "timeout": config.timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url

        self._client = AsyncAnthropic(**client_kwargs)

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _serialize_messages(
        self,
        messages: Sequence[LLMMessage],
        explicit_system_prompt: str | None,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        if explicit_system_prompt:
            system_parts.append(explicit_system_prompt)

        output: list[dict[str, Any]] = []

        def append_blocks(
            role: str,
            blocks: list[dict[str, Any]],
            *,
            tool_results_first: bool = False,
        ) -> None:
            if not blocks:
                return

            if output and output[-1]["role"] == role:
                existing = output[-1]["content"]
                if tool_results_first:
                    insertion_index = 0
                    while (
                        insertion_index < len(existing)
                        and existing[insertion_index].get("type") == "tool_result"
                    ):
                        insertion_index += 1
                    for block in reversed(blocks):
                        existing.insert(insertion_index, block)
                else:
                    existing.extend(blocks)
                return

            output.append({"role": role, "content": blocks})

        for message in messages:
            if message.role is MessageRole.SYSTEM:
                if message.content:
                    system_parts.append(message.content)
                continue

            if message.role is MessageRole.USER:
                append_blocks(
                    "user",
                    [{"type": "text", "text": message.content or ""}],
                )
                continue

            if message.role is MessageRole.ASSISTANT:
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for tool_call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "input": tool_call.arguments,
                        }
                    )
                append_blocks("assistant", blocks)
                continue

            if message.role is MessageRole.TOOL:
                append_blocks(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content or "",
                            "is_error": message.tool_is_error,
                        }
                    ],
                    tool_results_first=True,
                )
                continue

            raise LLMInvalidRequestError(
                f"Unsupported normalized message role: {message.role}",
                provider=self.provider,
                code="unsupported_message_role",
            )

        system = "\n\n".join(system_parts) or None
        return system, output

    @staticmethod
    def _serialize_tools(
        tools: Sequence[LLMToolDefinition],
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]

    @staticmethod
    def _serialize_tool_choice(
        options: LLMRequestOptions,
    ) -> dict[str, Any]:
        choice = options.tool_choice
        if choice is None or choice.mode is ToolChoiceMode.AUTO:
            payload: dict[str, Any] = {"type": "auto"}
        elif choice.mode is ToolChoiceMode.NONE:
            payload = {"type": "none"}
        elif choice.mode is ToolChoiceMode.REQUIRED:
            payload = {"type": "any"}
        else:
            payload = {"type": "tool", "name": choice.tool_name}

        if options.parallel_tool_calls is False:
            payload["disable_parallel_tool_use"] = True
        return payload

    def _build_request_params(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None,
        tools: Sequence[LLMToolDefinition] | None,
        options: LLMRequestOptions | None,
        stream: bool,
        provider_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        options = options or LLMRequestOptions()

        if options.seed is not None:
            raise LLMUnsupportedFeatureError(
                "Anthropic Messages API does not support the normalized seed option",
                provider=self.provider,
                code="seed_not_supported",
            )

        illegal = self._RESERVED_REQUEST_KEYS.intersection(provider_kwargs)
        if illegal:
            raise LLMInvalidRequestError(
                f"Provider kwargs cannot override reserved keys: {sorted(illegal)}",
                provider=self.provider,
                code="reserved_request_key",
            )

        serialized_system, serialized_messages = self._serialize_messages(
            messages,
            system_prompt,
        )
        if not serialized_messages:
            raise LLMInvalidRequestError(
                "Anthropic request requires at least one non-system message",
                provider=self.provider,
                code="empty_messages",
            )

        params = dict(self.config.default_params)
        params.update(
            {
                "model": options.model or self.model,
                "max_tokens": (options.max_tokens or self.config.default_max_tokens),
                "messages": serialized_messages,
                "stream": stream,
            }
        )

        if serialized_system:
            params["system"] = serialized_system
        if options.temperature is not None:
            params["temperature"] = options.temperature
        if options.top_p is not None:
            params["top_p"] = options.top_p
        if options.stop is not None:
            params["stop_sequences"] = (
                [options.stop] if isinstance(options.stop, str) else options.stop
            )
        if options.timeout_seconds is not None:
            params["timeout"] = options.timeout_seconds

        if tools:
            params["tools"] = self._serialize_tools(tools)
            params["tool_choice"] = self._serialize_tool_choice(options)

        if options.extra_headers:
            params["extra_headers"] = options.extra_headers
        if options.extra_query:
            params["extra_query"] = options.extra_query
        if options.extra_body:
            params["extra_body"] = options.extra_body

        params.update(provider_kwargs)
        return params

    @staticmethod
    def _normalize_finish_reason(stop_reason: str | None) -> FinishReason:
        mapping = {
            "end_turn": FinishReason.STOP,
            "stop_sequence": FinishReason.STOP,
            "tool_use": FinishReason.TOOL,
            "max_tokens": FinishReason.LENGTH,
            "refusal": FinishReason.CONTENT_FILTER,
        }
        return mapping.get(stop_reason, FinishReason.UNKNOWN)

    @staticmethod
    def _normalize_usage(usage: Any | None) -> TokenUsage:
        if usage is None:
            return TokenUsage()

        output_details = getattr(usage, "output_tokens_details", None)
        return TokenUsage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_input_tokens=(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            reasoning_tokens=(getattr(output_details, "thinking_tokens", 0) or 0),
        )

    def _normalize_response(self, message: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[NormalizedToolCall] = []
        content_types: list[str] = []

        for block in message.content:
            block_type = getattr(block, "type", "unknown")
            content_types.append(block_type)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(
                    NormalizedToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input),
                        raw_arguments=self._json_dumps(block.input),
                    )
                )

        return LLMResponse(
            content="".join(text_parts) or None,
            provider=self.provider,
            model=getattr(message, "model", self.model),
            response_id=getattr(message, "id", None),
            tool_calls=tool_calls,
            usage=self._normalize_usage(getattr(message, "usage", None)),
            finish_reason=self._normalize_finish_reason(getattr(message, "stop_reason", None)),
            raw_metadata={
                "stop_sequence": getattr(message, "stop_sequence", None),
                "content_block_types": content_types,
            },
        )

    def _parse_streamed_tool_arguments(
        self,
        raw_arguments: str,
        *,
        tool_name: str,
    ) -> tuple[dict[str, Any], str]:
        raw = raw_arguments or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMResponseFormatError(
                f"Tool '{tool_name}' returned invalid JSON arguments",
                provider=self.provider,
                code="invalid_tool_arguments",
                cause=exc,
                details={
                    "tool_name": tool_name,
                    "raw_arguments": raw,
                },
            ) from exc

        if not isinstance(parsed, dict):
            raise LLMResponseFormatError(
                f"Tool '{tool_name}' arguments must be a JSON object",
                provider=self.provider,
                code="tool_arguments_not_object",
                details={
                    "tool_name": tool_name,
                    "raw_arguments": raw,
                },
            )
        return parsed, raw

    def _map_exception(self, exc: Exception) -> Exception:
        request_id = getattr(exc, "request_id", None)
        status_code = getattr(exc, "status_code", None)

        if isinstance(exc, anthropic.AuthenticationError):
            return LLMAuthenticationError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="authentication_error",
                cause=exc,
            )
        if isinstance(exc, anthropic.PermissionDeniedError):
            return LLMPermissionError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="permission_denied",
                cause=exc,
            )
        if isinstance(exc, anthropic.RateLimitError):
            return LLMRateLimitError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="rate_limit",
                retryable=True,
                cause=exc,
            )
        if isinstance(exc, anthropic.APITimeoutError):
            return LLMTimeoutError(
                str(exc),
                provider=self.provider,
                request_id=request_id,
                code="timeout",
                retryable=True,
                cause=exc,
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMConnectionError(
                str(exc),
                provider=self.provider,
                request_id=request_id,
                code="connection_error",
                retryable=True,
                cause=exc,
            )
        if isinstance(exc, anthropic.BadRequestError):
            return LLMInvalidRequestError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="bad_request",
                cause=exc,
            )
        if isinstance(
            exc,
            (anthropic.InternalServerError, anthropic.APIStatusError),
        ):
            retryable = status_code is None or status_code >= 500
            return LLMProviderUnavailableError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="provider_error",
                retryable=retryable,
                cause=exc,
            )
        return exc

    async def invoke(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        **provider_kwargs: Any,
    ) -> LLMResponse:
        params = self._build_request_params(
            messages,
            system_prompt=system_prompt,
            tools=tools,
            options=options,
            stream=False,
            provider_kwargs=provider_kwargs,
        )
        try:
            message = await self._client.messages.create(**params)
            return self._normalize_response(message)
        except Exception as exc:
            mapped = self._map_exception(exc)
            if mapped is exc:
                raise
            raise mapped from exc

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        **provider_kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        params = self._build_request_params(
            messages,
            system_prompt=system_prompt,
            tools=tools,
            options=options,
            stream=True,
            provider_kwargs=provider_kwargs,
        )

        stream_obj: Any | None = None
        response_id: str | None = None
        actual_model = self.model
        text_parts: list[str] = []
        tool_buffers: dict[int, dict[str, Any]] = {}
        usage = TokenUsage()
        finish_reason = FinishReason.UNKNOWN

        try:
            stream_obj = await self._client.messages.create(**params)
            async for event in stream_obj:
                event_type = getattr(event, "type", None)

                if event_type == "message_start":
                    message = event.message
                    response_id = getattr(message, "id", None)
                    actual_model = getattr(message, "model", actual_model)
                    usage = self._normalize_usage(getattr(message, "usage", None))
                    continue

                if event_type == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        initial_input = getattr(block, "input", {}) or {}
                        tool_buffers[event.index] = {
                            "id": block.id,
                            "name": block.name,
                            "arguments": (self._json_dumps(initial_input) if initial_input else ""),
                        }
                        yield LLMStreamChunk(
                            event_type=StreamEventType.TOOL_CALL_DELTA,
                            provider=self.provider,
                            model=actual_model,
                            response_id=response_id,
                            tool_call_delta=ToolCallDelta(
                                index=event.index,
                                id=block.id,
                                name=block.name,
                            ),
                        )
                    continue

                if event_type == "content_block_delta":
                    delta = event.delta
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text_delta":
                        text_delta = delta.text
                        text_parts.append(text_delta)
                        yield LLMStreamChunk(
                            event_type=StreamEventType.TEXT_DELTA,
                            provider=self.provider,
                            model=actual_model,
                            response_id=response_id,
                            content_delta=text_delta,
                        )
                    elif delta_type == "input_json_delta":
                        partial_json = delta.partial_json
                        buffer = tool_buffers.setdefault(
                            event.index,
                            {"id": None, "name": "", "arguments": ""},
                        )
                        buffer["arguments"] += partial_json
                        yield LLMStreamChunk(
                            event_type=StreamEventType.TOOL_CALL_DELTA,
                            provider=self.provider,
                            model=actual_model,
                            response_id=response_id,
                            tool_call_delta=ToolCallDelta(
                                index=event.index,
                                id=buffer["id"],
                                name=buffer["name"] or None,
                                arguments_delta=partial_json,
                            ),
                        )
                    # thinking/signature deltas are deliberately ignored.
                    continue

                if event_type == "message_delta":
                    stop_reason = getattr(event.delta, "stop_reason", None)
                    if stop_reason:
                        finish_reason = self._normalize_finish_reason(stop_reason)

                    delta_usage = getattr(event, "usage", None)
                    if delta_usage is not None:
                        usage = TokenUsage(
                            input_tokens=usage.input_tokens,
                            output_tokens=(getattr(delta_usage, "output_tokens", 0) or 0),
                            cached_input_tokens=usage.cached_input_tokens,
                            cache_creation_input_tokens=(usage.cache_creation_input_tokens),
                            reasoning_tokens=usage.reasoning_tokens,
                        )
                        yield LLMStreamChunk(
                            event_type=StreamEventType.USAGE,
                            provider=self.provider,
                            model=actual_model,
                            response_id=response_id,
                            usage=usage,
                        )
                    continue

                if event_type == "error":
                    error = getattr(event, "error", None)
                    raise LLMProviderUnavailableError(
                        getattr(error, "message", "Anthropic stream error"),
                        provider=self.provider,
                        code=getattr(error, "type", "stream_error"),
                        retryable=True,
                    )
                # ping, block_stop, message_stop and unknown events are ignored.

            normalized_tool_calls: list[NormalizedToolCall] = []
            for index in sorted(tool_buffers):
                buffer = tool_buffers[index]
                name = buffer["name"]
                arguments, raw_arguments = self._parse_streamed_tool_arguments(
                    buffer["arguments"],
                    tool_name=name,
                )
                normalized_tool_calls.append(
                    NormalizedToolCall(
                        id=buffer["id"] or f"tool_call_{index}",
                        name=name,
                        arguments=arguments,
                        raw_arguments=raw_arguments,
                    )
                )

            response = LLMResponse(
                content="".join(text_parts) or None,
                provider=self.provider,
                model=actual_model,
                response_id=response_id,
                tool_calls=normalized_tool_calls,
                usage=usage,
                finish_reason=finish_reason,
            )
            yield LLMStreamChunk(
                event_type=StreamEventType.FINISH,
                provider=self.provider,
                model=actual_model,
                response_id=response_id,
                usage=usage,
                finish_reason=finish_reason,
                is_final=True,
                final_response=response,
            )
        except Exception as exc:
            mapped = self._map_exception(exc)
            if mapped is exc:
                raise
            raise mapped from exc
        finally:
            if stream_obj is not None:
                close_method = getattr(stream_obj, "close", None)
                if close_method is not None:
                    result = close_method()
                    if inspect.isawaitable(result):
                        await result

    async def close(self) -> None:
        await self._client.close()
