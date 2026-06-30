from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.llm.anthropic_provider import AnthropicAdapter
from app.llm.exceptions import LLMAllProvidersFailedError, LLMProviderUnavailableError
from app.llm.openai_provider import OpenAICompatibleAdapter, OpenAICompatibleConfig
from app.llm.schemas import (
    FinishReason,
    LLMMessage,
    LLMRequestOptions,
    LLMToolDefinition,
    MessageRole,
    NormalizedToolCall,
)


class ProviderNormalizationTests(unittest.TestCase):
    def test_openai_tool_calls_finish_reason_is_normalized(self) -> None:
        self.assertEqual(
            FinishReason.TOOL,
            OpenAICompatibleAdapter._normalize_finish_reason("tool_calls"),
        )

    def test_anthropic_tool_use_finish_reason_is_normalized(self) -> None:
        self.assertEqual(
            FinishReason.TOOL,
            AnthropicAdapter._normalize_finish_reason("tool_use"),
        )


class OpenAIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_name_deltas_are_concatenated_without_deduplication(self) -> None:
        class FakeStream:
            def __init__(self) -> None:
                self.chunks = [
                    SimpleNamespace(
                        id="response-1",
                        model="test-model",
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id="call-1",
                                            function=SimpleNamespace(name="query_", arguments="{}"),
                                        )
                                    ],
                                ),
                                finish_reason=None,
                            )
                        ],
                    ),
                    SimpleNamespace(
                        id="response-1",
                        model="test-model",
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id=None,
                                            function=SimpleNamespace(name="query_", arguments=None),
                                        )
                                    ],
                                ),
                                finish_reason="tool_calls",
                            )
                        ],
                    ),
                ]

            def __aiter__(self):
                return self._iterate()

            async def _iterate(self):
                for chunk in self.chunks:
                    yield chunk

            async def close(self) -> None:
                return None

        class FakeCompletions:
            async def create(self, **params):
                return FakeStream()

        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(model="test-model", api_key="sk-test")
        )
        adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        chunks = [
            chunk
            async for chunk in adapter.stream([LLMMessage(role=MessageRole.USER, content="run")])
        ]

        self.assertEqual("query_query_", chunks[-1].final_response.tool_calls[0].name)


class PerRequestModelOverrideTests(unittest.TestCase):
    def _adapter(self) -> OpenAICompatibleAdapter:
        return OpenAICompatibleAdapter(OpenAICompatibleConfig(model="gpt-4o", api_key="sk-test"))

    def test_request_model_overrides_configured_model(self) -> None:
        adapter = self._adapter()
        params = adapter._build_request_params(
            [LLMMessage(role=MessageRole.USER, content="hi")],
            system_prompt=None,
            tools=None,
            options=LLMRequestOptions(model="gpt-4o-mini"),
            stream=False,
            provider_kwargs={},
        )
        self.assertEqual("gpt-4o-mini", params["model"])

    def test_falls_back_to_configured_model_when_unset(self) -> None:
        adapter = self._adapter()
        params = adapter._build_request_params(
            [LLMMessage(role=MessageRole.USER, content="hi")],
            system_prompt=None,
            tools=None,
            options=None,
            stream=False,
            provider_kwargs={},
        )
        self.assertEqual("gpt-4o", params["model"])

    def test_assistant_tool_call_history_omits_natural_language_content(self) -> None:
        adapter = self._adapter()
        params = adapter._build_request_params(
            [
                LLMMessage(
                    role=MessageRole.ASSISTANT,
                    content="Tôi sẽ kiểm tra bảng trước.",
                    tool_calls=[
                        NormalizedToolCall(
                            id="call-1",
                            name="query_clickhouse",
                            arguments={"sql": "SHOW TABLES"},
                        )
                    ],
                ),
                LLMMessage(
                    role=MessageRole.TOOL,
                    tool_call_id="call-1",
                    content='[{"name":"alarm"}]',
                ),
            ],
            system_prompt=None,
            tools=None,
            options=None,
            stream=False,
            provider_kwargs={},
        )

        assistant_message = params["messages"][0]
        self.assertIsNone(assistant_message["content"])
        self.assertEqual("call-1", assistant_message["tool_calls"][0]["id"])

    def test_tool_strict_serialization_uses_provider_capability_not_model_name(self) -> None:
        tool = LLMToolDefinition(
            name="query_clickhouse",
            description="Query ClickHouse.",
            input_schema={
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
                "additionalProperties": False,
            },
        )

        strict_capable = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                model="mistral-large",
                api_key="sk-test",
                supports_tool_strict=True,
            )
        )
        strict_params = strict_capable._build_request_params(
            [LLMMessage(role=MessageRole.USER, content="hi")],
            system_prompt=None,
            tools=[tool],
            options=LLMRequestOptions(model="mimo-router-model"),
            stream=False,
            provider_kwargs={},
        )

        strict_function = strict_params["tools"][0]["function"]
        self.assertTrue(strict_function["strict"])

        non_strict_capable = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                model="gpt-4o",
                api_key="sk-test",
                supports_tool_strict=False,
            )
        )
        non_strict_params = non_strict_capable._build_request_params(
            [LLMMessage(role=MessageRole.USER, content="hi")],
            system_prompt=None,
            tools=[tool],
            options=LLMRequestOptions(model="gpt-4.1"),
            stream=False,
            provider_kwargs={},
        )

        non_strict_function = non_strict_params["tools"][0]["function"]
        self.assertNotIn("strict", non_strict_function)

    def test_tool_definition_can_opt_out_of_strict_when_provider_supports_it(self) -> None:
        tool = LLMToolDefinition(
            name="query_clickhouse",
            description="Query ClickHouse.",
            input_schema={
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
                "additionalProperties": False,
            },
            strict=False,
        )
        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                model="gpt-4o",
                api_key="sk-test",
                supports_tool_strict=True,
            )
        )

        params = adapter._build_request_params(
            [LLMMessage(role=MessageRole.USER, content="hi")],
            system_prompt=None,
            tools=[tool],
            options=None,
            stream=False,
            provider_kwargs={},
        )

        self.assertNotIn("strict", params["tools"][0]["function"])


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
