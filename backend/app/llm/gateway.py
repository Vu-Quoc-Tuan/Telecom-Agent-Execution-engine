import asyncio
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from typing import Any

from app.llm.base import BaseLLMAdapter
from app.llm.exceptions import (
    LLMAllProvidersFailedError,
    LLMConfigurationError,
    LLMError,
)
from app.llm.schemas import (
    LLMMessage,
    LLMRequestOptions,
    LLMResponse,
    LLMStreamChunk,
    LLMToolDefinition,
)


class LLMGateway:
    """
    Provider registry and provider-independent entry point.

    Streaming fallback is only attempted before the first chunk is emitted.
    Switching providers after partial output would duplicate or corrupt output.
    """

    def __init__(
        self,
        adapters: Iterable[BaseLLMAdapter] | None = None,
        *,
        default_provider: str | None = None,
    ) -> None:
        self._adapters: dict[str, BaseLLMAdapter] = {}
        self._default_provider = default_provider

        for adapter in adapters or ():
            self.register(adapter)

        if self._default_provider is not None and self._default_provider not in self._adapters:
            raise LLMConfigurationError(
                f"Default provider '{self._default_provider}' is not registered",
                provider="gateway",
                code="default_provider_not_registered",
            )

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._adapters.keys())

    def register(
        self,
        adapter: BaseLLMAdapter,
        *,
        replace: bool = False,
    ) -> None:
        if adapter.provider in self._adapters and not replace:
            raise LLMConfigurationError(
                f"Provider '{adapter.provider}' is already registered",
                provider="gateway",
                code="duplicate_provider",
            )
        self._adapters[adapter.provider] = adapter

        if self._default_provider is None:
            self._default_provider = adapter.provider

    def get_adapter(self, provider: str | None = None) -> BaseLLMAdapter:
        selected = provider or self._default_provider
        if selected is None:
            raise LLMConfigurationError(
                "No LLM provider was selected and no default provider exists",
                provider="gateway",
                code="provider_not_selected",
            )

        adapter = self._adapters.get(selected)
        if adapter is None:
            raise LLMConfigurationError(
                f"Unsupported LLM provider: {selected}",
                provider="gateway",
                code="provider_not_registered",
                details={"registered_providers": list(self._adapters)},
            )
        return adapter

    @staticmethod
    def _provider_order(
        primary: str | None,
        fallbacks: Sequence[str] | None,
    ) -> list[str | None]:
        order: list[str | None] = [primary]
        for provider in fallbacks or ():
            if provider not in order:
                order.append(provider)
        return order

    async def invoke(
        self,
        messages: Sequence[LLMMessage],
        *,
        provider: str | None = None,
        fallback_providers: Sequence[str] | None = None,
        fallback_on_non_retryable: bool = False,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        provider_options: Mapping[str, LLMRequestOptions] | None = None,
        **provider_kwargs: Any,
    ) -> LLMResponse:
        errors: list[LLMError] = []

        for selected in self._provider_order(provider, fallback_providers):
            adapter = self.get_adapter(selected)
            selected_options = (provider_options or {}).get(adapter.provider, options)
            try:
                return await adapter.invoke(
                    messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    options=selected_options,
                    **provider_kwargs,
                )
            except LLMError as error:
                errors.append(error)
                if not error.retryable and not fallback_on_non_retryable:
                    raise

        raise LLMAllProvidersFailedError(errors=errors)

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        provider: str | None = None,
        fallback_providers: Sequence[str] | None = None,
        fallback_on_non_retryable: bool = False,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        provider_options: Mapping[str, LLMRequestOptions] | None = None,
        **provider_kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        errors: list[LLMError] = []

        for selected in self._provider_order(provider, fallback_providers):
            adapter = self.get_adapter(selected)
            selected_options = (provider_options or {}).get(adapter.provider, options)
            emitted = False
            try:
                async for chunk in adapter.stream(
                    messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    options=selected_options,
                    **provider_kwargs,
                ):
                    emitted = True
                    yield chunk
                return
            except LLMError as error:
                if emitted:
                    raise
                errors.append(error)
                if not error.retryable and not fallback_on_non_retryable:
                    raise

        raise LLMAllProvidersFailedError(errors=errors)

    async def close(self) -> None:
        results = await asyncio.gather(
            *(adapter.close() for adapter in self._adapters.values()),
            return_exceptions=True,
        )
        errors = [item for item in results if isinstance(item, Exception)]
        if errors:
            raise RuntimeError(f"Failed to close {len(errors)} LLM adapter(s)") from errors[0]
