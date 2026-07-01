from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessageChunk

from app.llm.base import BaseLLMAdapter
from app.llm.exceptions import (
    LLMError,
    LLMInvalidRequestError,
    LLMProviderUnavailableError,
)
from app.llm.langchain_messages import (
    model_name,
    normalize_response,
    to_langchain_messages,
)
from app.llm.langchain_model import (
    LangChainChatConfig,
    bind_tools_if_needed,
    build_chat_model,
    model_call_kwargs,
    runnable_config,
    select_chat_model,
)
from app.llm.schemas import (
    LLMMessage,
    LLMRequestOptions,
    LLMResponse,
    LLMStreamChunk,
    LLMToolDefinition,
    StreamEventType,
)


class LangChainChatAdapter(BaseLLMAdapter):
    """Project LLM contract backed by LangChain chat models."""

    def __init__(self, config: LangChainChatConfig) -> None:
        super().__init__(config)
        self.config = config
        self._chat_model = build_chat_model(config)

    def _prepare_model(
        self,
        tools: Sequence[LLMToolDefinition] | None,
        options: LLMRequestOptions | None,
    ):
        model = select_chat_model(self._chat_model, self.config, options)
        return bind_tools_if_needed(model, self.config, tools, options)

    def _wrap_exception(self, exc: Exception) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, (ValueError, TypeError)):
            return LLMInvalidRequestError(
                str(exc),
                provider=self.provider,
                code=exc.__class__.__name__,
                retryable=False,
                cause=exc,
            )
        return LLMProviderUnavailableError(
            str(exc),
            provider=self.provider,
            code=exc.__class__.__name__,
            retryable=True,
            cause=exc,
        )

    async def invoke(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        **provider_kwargs: Any,
    ) -> LLMResponse:
        try:
            response = await self._prepare_model(tools, options).ainvoke(
                to_langchain_messages(messages, system_prompt),
                config=runnable_config(provider_kwargs),
                **model_call_kwargs(options, provider_kwargs),
            )
            return normalize_response(
                response,
                provider=self.provider,
                fallback_model=self.model,
            )
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        **provider_kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        try:
            full_message: AIMessageChunk | None = None
            async for chunk in self._prepare_model(tools, options).astream(
                to_langchain_messages(messages, system_prompt),
                config=runnable_config(provider_kwargs),
                **model_call_kwargs(options, provider_kwargs),
            ):
                full_message = chunk if full_message is None else full_message + chunk
                if chunk.text:
                    yield LLMStreamChunk(
                        event_type=StreamEventType.TEXT_DELTA,
                        provider=self.provider,
                        model=model_name(chunk, self.model),
                        response_id=chunk.id,
                        content_delta=chunk.text,
                    )

            final_response = normalize_response(
                full_message or AIMessageChunk(content=""),
                provider=self.provider,
                fallback_model=self.model,
            )
            yield LLMStreamChunk(
                event_type=StreamEventType.FINISH,
                provider=self.provider,
                model=final_response.model,
                response_id=final_response.response_id,
                usage=final_response.usage,
                finish_reason=final_response.finish_reason,
                is_final=True,
                final_response=final_response,
            )
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def close(self) -> None:
        return None
