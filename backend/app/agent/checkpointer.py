from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.config import settings as default_settings
from app.llm.schemas import (
    FinishReason,
    LLMMessage,
    LLMResponse,
    MessageRole,
    NormalizedToolCall,
    TokenUsage,
)


def build_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=()).with_msgpack_allowlist(
        [FinishReason, LLMMessage, LLMResponse, MessageRole, NormalizedToolCall, TokenUsage]
    )


class WorkflowCheckpointer:
    def __init__(
        self, *, settings=default_settings, backend: str | None = None, db_url: str | None = None
    ) -> None:
        self.settings = settings
        self.backend = backend or settings.CHECKPOINTER_BACKEND
        self.db_url = db_url or settings.checkpointer_database_url
        self.saver: Any | None = None
        self._exit_stack: AsyncExitStack | None = None

    async def initialize(self):
        if self.saver is not None:
            return self.saver

        if self.backend == "memory":
            self.saver = InMemorySaver()
            return self.saver

        if self.backend != "postgres":
            raise ValueError(f"Unsupported checkpointer backend: {self.backend}")

        self._exit_stack = AsyncExitStack()
        self.saver = await self._exit_stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(
                self.db_url,
                serde=build_checkpoint_serializer(),
            )
        )
        await self.saver.setup()
        return self.saver

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self.saver = None
