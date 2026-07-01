from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, AIMessageChunk

from app.llm.exceptions import LLMAllProvidersFailedError, LLMProviderUnavailableError
from app.llm.schemas import (
    FinishReason,
    LLMMessage,
    LLMRequestOptions,
    LLMToolDefinition,
    MessageRole,
    ToolChoice,
    ToolChoiceMode,
)


class LangChainChatAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_normalizes_langchain_tool_calls_and_usage(self) -> None:
        from app.llm.langchain_adapter import LangChainChatAdapter
        from app.llm.langchain_model import LangChainChatConfig

        class FakeChatModel:
            def __init__(self) -> None:
                self.bound_tools = None
                self.bound_kwargs = None
                self.messages = None
                self.config = None

            def bind_tools(self, tools, **kwargs):
                self.bound_tools = tools
                self.bound_kwargs = kwargs
                return self

            async def ainvoke(self, messages, config=None):
                self.messages = messages
                self.config = config
                return AIMessage(
                    content="",
                    id="msg-1",
                    tool_calls=[
                        {
                            "name": "get_active_alarms",
                            "args": {"limit": 5},
                            "id": "call-1",
                        }
                    ],
                    usage_metadata={
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "total_tokens": 18,
                    },
                    response_metadata={
                        "model_name": "fake-model",
                        "finish_reason": "tool_calls",
                    },
                )

        fake_model = FakeChatModel()
        adapter = LangChainChatAdapter(
            LangChainChatConfig(provider="openai", model="gpt-4o", api_key="sk-test")
        )
        adapter._chat_model = fake_model
        response = await adapter.invoke(
            [LLMMessage(role=MessageRole.USER, content="show alarms")],
            system_prompt="system prompt",
            tools=[
                LLMToolDefinition(
                    name="get_active_alarms",
                    description="List active alarms.",
                    input_schema={
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                        "required": ["limit"],
                    },
                )
            ],
            options=LLMRequestOptions(
                tool_choice=ToolChoice(mode=ToolChoiceMode.SPECIFIC, tool_name="get_active_alarms"),
                parallel_tool_calls=False,
            ),
            callbacks=["callback"],
        )

        self.assertEqual(FinishReason.TOOL, response.finish_reason)
        self.assertEqual("fake-model", response.model)
        self.assertEqual("call-1", response.tool_calls[0].id)
        self.assertEqual({"limit": 5}, response.tool_calls[0].arguments)
        self.assertEqual(11, response.usage.input_tokens)
        self.assertEqual(["callback"], fake_model.config["callbacks"])
        self.assertEqual("get_active_alarms", fake_model.bound_kwargs["tool_choice"])
        self.assertFalse(fake_model.bound_kwargs["parallel_tool_calls"])
        self.assertTrue(fake_model.bound_tools[0]["function"]["strict"])

    def test_non_strict_tool_keeps_optional_schema_fields(self) -> None:
        from app.llm.langchain_model import to_langchain_tools

        payload = to_langchain_tools(
            [
                LLMToolDefinition(
                    name="search",
                    description="Search with an optional filter.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "filter": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                    strict=False,
                )
            ],
            supports_strict=True,
        )[0]["function"]

        self.assertNotIn("strict", payload)
        self.assertEqual(["query"], payload["parameters"]["required"])

    async def test_tool_choice_none_skips_tool_binding(self) -> None:
        from app.llm.langchain_adapter import LangChainChatAdapter
        from app.llm.langchain_model import LangChainChatConfig

        class FakeChatModel:
            def bind_tools(self, tools, **kwargs):
                raise AssertionError("tools must not be bound when tool_choice is none")

            async def ainvoke(self, messages, config=None):
                return AIMessage(
                    content="No tool call",
                    response_metadata={"model_name": "fake", "finish_reason": "stop"},
                )

        adapter = LangChainChatAdapter(
            LangChainChatConfig(provider="anthropic", model="claude-test", api_key="sk-test")
        )
        adapter._chat_model = FakeChatModel()

        response = await adapter.invoke(
            [LLMMessage(role=MessageRole.USER, content="continue")],
            tools=[
                LLMToolDefinition(
                    name="get_active_alarms",
                    description="List active alarms.",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            options=LLMRequestOptions(
                tool_choice=ToolChoice(mode=ToolChoiceMode.NONE),
                parallel_tool_calls=False,
            ),
        )

        self.assertEqual("No tool call", response.content)
        self.assertEqual(FinishReason.STOP, response.finish_reason)

    async def test_forwards_stop_sequences_to_langchain_model(self) -> None:
        from app.llm.langchain_adapter import LangChainChatAdapter
        from app.llm.langchain_model import LangChainChatConfig

        class FakeChatModel:
            def __init__(self) -> None:
                self.call_kwargs = None

            async def ainvoke(self, messages, config=None, **kwargs):
                self.call_kwargs = kwargs
                return AIMessage(
                    content="done",
                    response_metadata={"model_name": "fake", "finish_reason": "stop"},
                )

        fake_model = FakeChatModel()
        adapter = LangChainChatAdapter(
            LangChainChatConfig(provider="openai", model="gpt-4o", api_key="sk-test")
        )
        adapter._chat_model = fake_model

        await adapter.invoke(
            [LLMMessage(role=MessageRole.USER, content="hi")],
            options=LLMRequestOptions(stop=["DONE", "END"]),
        )

        self.assertEqual(["DONE", "END"], fake_model.call_kwargs["stop"])

    async def test_stream_emits_text_deltas_and_final_response(self) -> None:
        from app.llm.langchain_adapter import LangChainChatAdapter
        from app.llm.langchain_model import LangChainChatConfig

        class FakeChatModel:
            async def astream(self, messages, config=None):
                yield AIMessageChunk(content="hel", response_metadata={"model_name": "fake"})
                yield AIMessageChunk(
                    content="lo",
                    id="msg-2",
                    usage_metadata={
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                    response_metadata={"model_name": "fake", "finish_reason": "stop"},
                )

        adapter = LangChainChatAdapter(
            LangChainChatConfig(provider="openai", model="gpt-4o", api_key="sk-test")
        )
        adapter._chat_model = FakeChatModel()

        chunks = [
            chunk
            async for chunk in adapter.stream([LLMMessage(role=MessageRole.USER, content="hi")])
        ]

        self.assertEqual(["hel", "lo"], [c.content_delta for c in chunks[:2]])
        self.assertTrue(chunks[-1].is_final)
        self.assertEqual("hello", chunks[-1].final_response.content)
        self.assertEqual(5, chunks[-1].final_response.usage.total_tokens)


class LLMConfigTests(unittest.TestCase):
    def test_openai_tool_strict_capability_defaults_to_off_for_custom_router(self) -> None:
        from app.config import Settings

        official = Settings(_env_file=None, OPENAI_API_URL="https://api.openai.com/v1")
        custom_router = Settings(_env_file=None, OPENAI_API_URL="https://router.example.test/v1")
        explicit = Settings(
            _env_file=None,
            OPENAI_API_URL="https://router.example.test/v1",
            OPENAI_SUPPORTS_TOOL_STRICT=True,
        )

        self.assertTrue(official.openai_supports_tool_strict)
        self.assertFalse(custom_router.openai_supports_tool_strict)
        self.assertTrue(explicit.openai_supports_tool_strict)


class LLMGatewayErrorTests(unittest.TestCase):
    def test_single_provider_failure_message_keeps_provider_reason(self) -> None:
        error = LLMProviderUnavailableError(
            "No available channel for model gpt-5.5",
            provider="openai",
            code="provider_unavailable",
            status_code=503,
            retryable=True,
        )

        combined = LLMAllProvidersFailedError(errors=[error])

        self.assertIn("All configured LLM providers failed: openai", combined.message)
        self.assertIn("No available channel for model gpt-5.5", combined.message)


class LLMGatewayFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_provider_receives_its_own_model_options(self) -> None:
        from app.llm.gateway import LLMGateway
        from app.llm.schemas import LLMResponse

        class FakeAdapter:
            def __init__(self, provider: str, model: str, *, fail: bool = False):
                self.provider = provider
                self.model = model
                self.fail = fail
                self.received_options = None

            async def invoke(self, messages, *, options=None, **kwargs):
                self.received_options = options
                if self.fail:
                    raise LLMProviderUnavailableError(
                        "provider unavailable",
                        provider=self.provider,
                        retryable=True,
                    )
                return LLMResponse(
                    provider=self.provider,
                    model=options.model,
                    content="done",
                    finish_reason=FinishReason.STOP,
                )

            async def stream(self, messages, **kwargs):
                if False:
                    yield None

            async def close(self):
                return None

        openai = FakeAdapter("openai", "gpt-default", fail=True)
        anthropic = FakeAdapter("anthropic", "claude-default")
        gateway = LLMGateway([openai, anthropic], default_provider="openai")

        response = await gateway.invoke(
            [LLMMessage(role=MessageRole.USER, content="hello")],
            provider="openai",
            fallback_providers=["anthropic"],
            options=LLMRequestOptions(model="gpt-selected"),
            provider_options={
                "anthropic": LLMRequestOptions(model="claude-default"),
            },
        )

        self.assertEqual("anthropic", response.provider)
        self.assertEqual("gpt-selected", openai.received_options.model)
        self.assertEqual("claude-default", anthropic.received_options.model)

    async def test_stream_falls_back_before_any_chunk_is_emitted(self) -> None:
        from app.llm.gateway import LLMGateway
        from app.llm.schemas import LLMResponse, LLMStreamChunk, StreamEventType

        class FakeStreamAdapter:
            def __init__(self, provider: str, model: str, *, fail: bool = False):
                self.provider = provider
                self.model = model
                self.fail = fail
                self.received_options = None

            async def invoke(self, messages, **kwargs):
                raise AssertionError("stream path expected")

            async def stream(self, messages, *, options=None, **kwargs):
                self.received_options = options
                if self.fail:
                    raise LLMProviderUnavailableError(
                        "provider unavailable",
                        provider=self.provider,
                        retryable=True,
                    )
                response = LLMResponse(
                    provider=self.provider,
                    model=options.model,
                    content="fallback stream",
                    finish_reason=FinishReason.STOP,
                )
                yield LLMStreamChunk(
                    event_type=StreamEventType.FINISH,
                    provider=self.provider,
                    model=options.model,
                    is_final=True,
                    final_response=response,
                )

            async def close(self):
                return None

        openai = FakeStreamAdapter("openai", "gpt-default", fail=True)
        anthropic = FakeStreamAdapter("anthropic", "claude-default")
        gateway = LLMGateway([openai, anthropic], default_provider="openai")

        chunks = [
            chunk
            async for chunk in gateway.stream(
                [LLMMessage(role=MessageRole.USER, content="hello")],
                provider="openai",
                fallback_providers=["anthropic"],
                options=LLMRequestOptions(model="gpt-selected"),
                provider_options={
                    "anthropic": LLMRequestOptions(model="claude-default"),
                },
            )
        ]

        self.assertEqual("anthropic", chunks[-1].provider)
        self.assertEqual("claude-default", anthropic.received_options.model)


if __name__ == "__main__":
    unittest.main()
