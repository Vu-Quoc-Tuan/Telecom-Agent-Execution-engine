import inspect
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

import openai
from openai import AsyncOpenAI
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


class OpenAICompatibleConfig(LLMAdapterConfig):
    """Configuration for OpenAI-compatible Chat Completions APIs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "openai"
    organization: str | None = None
    project: str | None = None
    max_tokens_field: Literal[
        "max_tokens",
        "max_completion_tokens",
    ] = "max_tokens"
    include_stream_usage: bool = False


class OpenAICompatibleAdapter(BaseLLMAdapter):
    _RESERVED_REQUEST_KEYS = {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
    }

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        super().__init__(config)
        self.config = config

        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key.get_secret_value(),
            "timeout": config.timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        if config.organization:
            client_kwargs["organization"] = config.organization
        if config.project:
            client_kwargs["project"] = config.project
        if config.default_headers:
            client_kwargs["default_headers"] = config.default_headers

        self._client = AsyncOpenAI(**client_kwargs)

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _serialize_messages(
        self,
        messages: Sequence[LLMMessage],
        system_prompt: str | None,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []

        if system_prompt:
            output.append({"role": "system", "content": system_prompt})

        for message in messages:
            if message.role is MessageRole.ASSISTANT:
                payload: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content,
                }
                if message.tool_calls:
                    payload["tool_calls"] = [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": self._json_dumps(tool_call.arguments),
                            },
                        }
                        for tool_call in message.tool_calls
                    ]
                output.append(payload)
                continue

            if message.role is MessageRole.TOOL:
                output.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content or "",
                    }
                )
                continue

            payload = {
                "role": message.role.value,
                "content": message.content or "",
            }
            if message.name:
                payload["name"] = message.name
            output.append(payload)

        return output

    @staticmethod
    def _serialize_tools(
        tools: Sequence[LLMToolDefinition],
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": tool.strict,
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _serialize_tool_choice(
        options: LLMRequestOptions,
    ) -> str | dict[str, Any] | None:
        choice = options.tool_choice
        if choice is None:
            return None
        if choice.mode is ToolChoiceMode.SPECIFIC:
            return {
                "type": "function",
                "function": {"name": choice.tool_name},
            }
        return choice.mode.value

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

        illegal = self._RESERVED_REQUEST_KEYS.intersection(provider_kwargs)
        if illegal:
            raise LLMInvalidRequestError(
                f"Provider kwargs cannot override reserved keys: {sorted(illegal)}",
                provider=self.provider,
                code="reserved_request_key",
            )

        params = dict(self.config.default_params)
        params.update(
            {
                "model": options.model or self.model,
                "messages": self._serialize_messages(messages, system_prompt),
                "stream": stream,
            }
        )

        max_tokens = options.max_tokens or self.config.default_max_tokens
        params[self.config.max_tokens_field] = max_tokens

        if options.temperature is not None:
            params["temperature"] = options.temperature
        if options.top_p is not None:
            params["top_p"] = options.top_p
        if options.stop is not None:
            params["stop"] = options.stop
        if options.seed is not None:
            params["seed"] = options.seed
        if options.timeout_seconds is not None:
            params["timeout"] = options.timeout_seconds

        if tools:
            params["tools"] = self._serialize_tools(tools)
            tool_choice = self._serialize_tool_choice(options)
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
            if options.parallel_tool_calls is not None:
                params["parallel_tool_calls"] = options.parallel_tool_calls

        if options.extra_headers:
            params["extra_headers"] = options.extra_headers
        if options.extra_query:
            params["extra_query"] = options.extra_query
        if options.extra_body:
            params["extra_body"] = options.extra_body
        if stream and self.config.include_stream_usage:
            params["stream_options"] = {"include_usage": True}

        params.update(provider_kwargs)
        return params

    def _parse_tool_arguments(
        self,
        raw_arguments: str | dict[str, Any] | None,
        *,
        tool_name: str,
    ) -> tuple[dict[str, Any], str | None]:
        if isinstance(raw_arguments, dict):
            return raw_arguments, self._json_dumps(raw_arguments)

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

    @staticmethod
    def _normalize_finish_reason(
        finish_reason: str | None,
    ) -> FinishReason:
        mapping = {
            "stop": FinishReason.STOP,
            "tool_calls": FinishReason.TOOL,
            "function_call": FinishReason.TOOL,
            "length": FinishReason.LENGTH,
            "content_filter": FinishReason.CONTENT_FILTER,
        }
        return mapping.get(finish_reason, FinishReason.UNKNOWN)

    @staticmethod
    def _normalize_usage(usage: Any | None) -> TokenUsage:
        if usage is None:
            return TokenUsage()

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        completion_details = getattr(
            usage,
            "completion_tokens_details",
            None,
        )
        return TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            cached_input_tokens=(getattr(prompt_details, "cached_tokens", 0) or 0),
            reasoning_tokens=(getattr(completion_details, "reasoning_tokens", 0) or 0),
        )

    def _normalize_response(self, completion: Any) -> LLMResponse:
        if not completion.choices:
            raise LLMResponseFormatError(
                "OpenAI-compatible provider returned no choices",
                provider=self.provider,
                code="empty_choices",
            )

        choice = completion.choices[0]
        message = choice.message
        tool_calls: list[NormalizedToolCall] = []

        for raw_call in message.tool_calls or []:
            function = raw_call.function
            arguments, raw_arguments = self._parse_tool_arguments(
                function.arguments,
                tool_name=function.name,
            )
            tool_calls.append(
                NormalizedToolCall(
                    id=raw_call.id,
                    name=function.name,
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                )
            )

        return LLMResponse(
            content=message.content,
            provider=self.provider,
            model=getattr(completion, "model", self.model),
            response_id=getattr(completion, "id", None),
            tool_calls=tool_calls,
            usage=self._normalize_usage(getattr(completion, "usage", None)),
            finish_reason=self._normalize_finish_reason(choice.finish_reason),
            raw_metadata={
                "system_fingerprint": getattr(
                    completion,
                    "system_fingerprint",
                    None,
                )
            },
        )

    def _map_exception(self, exc: Exception) -> Exception:
        request_id = getattr(exc, "request_id", None)
        status_code = getattr(exc, "status_code", None)

        if isinstance(exc, openai.AuthenticationError):
            return LLMAuthenticationError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="authentication_error",
                cause=exc,
            )
        if isinstance(exc, openai.PermissionDeniedError):
            return LLMPermissionError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="permission_denied",
                cause=exc,
            )
        if isinstance(exc, openai.RateLimitError):
            return LLMRateLimitError(
                str(exc),
                provider=self.provider,
                status_code=status_code,
                request_id=request_id,
                code="rate_limit",
                retryable=True,
                cause=exc,
            )
        if isinstance(exc, openai.APITimeoutError):
            return LLMTimeoutError(
                str(exc),
                provider=self.provider,
                request_id=request_id,
                code="timeout",
                retryable=True,
                cause=exc,
            )
        if isinstance(exc, openai.APIConnectionError):
            return LLMConnectionError(
                str(exc),
                provider=self.provider,
                request_id=request_id,
                code="connection_error",
                retryable=True,
                cause=exc,
            )
        if isinstance(exc, openai.BadRequestError):
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
            (openai.InternalServerError, openai.APIStatusError),
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
            completion = await self._client.chat.completions.create(**params)
            return self._normalize_response(completion)
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
            stream_obj = await self._client.chat.completions.create(**params)
            async for chunk in stream_obj:
                response_id = response_id or getattr(chunk, "id", None)
                actual_model = getattr(chunk, "model", actual_model)

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = self._normalize_usage(chunk_usage)
                    yield LLMStreamChunk(
                        event_type=StreamEventType.USAGE,
                        provider=self.provider,
                        model=actual_model,
                        response_id=response_id,
                        usage=usage,
                    )

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.delta
                content_delta = getattr(delta, "content", None)
                if content_delta:
                    text_parts.append(content_delta)
                    yield LLMStreamChunk(
                        event_type=StreamEventType.TEXT_DELTA,
                        provider=self.provider,
                        model=actual_model,
                        response_id=response_id,
                        content_delta=content_delta,
                    )

                for raw_delta in getattr(delta, "tool_calls", None) or []:
                    index = raw_delta.index
                    buffer = tool_buffers.setdefault(
                        index,
                        {"id": None, "name": "", "arguments": ""},
                    )
                    if raw_delta.id:
                        buffer["id"] = raw_delta.id

                    function = raw_delta.function
                    name_delta = getattr(function, "name", None)
                    arguments_delta = getattr(function, "arguments", None)
                    if name_delta:
                        if not buffer["name"]:
                            buffer["name"] = name_delta
                        elif not buffer["name"].endswith(name_delta):
                            buffer["name"] += name_delta
                    if arguments_delta:
                        buffer["arguments"] += arguments_delta

                    yield LLMStreamChunk(
                        event_type=StreamEventType.TOOL_CALL_DELTA,
                        provider=self.provider,
                        model=actual_model,
                        response_id=response_id,
                        tool_call_delta=ToolCallDelta(
                            index=index,
                            id=raw_delta.id,
                            name=name_delta,
                            arguments_delta=arguments_delta,
                        ),
                    )

                if choice.finish_reason:
                    finish_reason = self._normalize_finish_reason(choice.finish_reason)

            normalized_tool_calls: list[NormalizedToolCall] = []
            for index in sorted(tool_buffers):
                buffer = tool_buffers[index]
                name = buffer["name"]
                arguments, raw_arguments = self._parse_tool_arguments(
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
