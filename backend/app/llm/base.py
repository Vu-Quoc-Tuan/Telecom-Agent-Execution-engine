from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.llm.schemas import (
    LLMMessage,
    LLMRequestOptions,
    LLMResponse,
    LLMStreamChunk,
    LLMToolDefinition,
)


class LLMAdapterConfig(BaseModel):
    """Shared configuration for all provider adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    api_key: SecretStr

    base_url: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    default_max_tokens: int = Field(default=4096, gt=0)

    default_params: dict[str, Any] = Field(default_factory=dict)


class BaseLLMAdapter(ABC):
    """Contract implemented by every LLM provider adapter."""

    def __init__(self, config: LLMAdapterConfig) -> None:
        self.config = config

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def model(self) -> str:
        return self.config.model

    @abstractmethod
    async def invoke(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        **provider_kwargs: Any,
    ) -> LLMResponse:
        """Return one fully accumulated response."""
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_prompt: str | None = None,
        tools: Sequence[LLMToolDefinition] | None = None,
        options: LLMRequestOptions | None = None,
        **provider_kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Yield normalized streaming events."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release HTTP clients and connection pools."""
        raise NotImplementedError

    async def __aenter__(self) -> "BaseLLMAdapter":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()
