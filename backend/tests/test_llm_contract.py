from __future__ import annotations

import unittest

from app.llm.anthropic_provider import AnthropicAdapter
from app.llm.exceptions import LLMAllProvidersFailedError, LLMProviderUnavailableError
from app.llm.openai_provider import OpenAICompatibleAdapter, OpenAICompatibleConfig
from app.llm.schemas import FinishReason, LLMMessage, LLMRequestOptions, MessageRole


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


class LLMConfigTests(unittest.TestCase):
    def test_llm_default_headers_parse_from_json_object(self) -> None:
        from app.config import Settings

        settings = Settings(LLM_DEFAULT_HEADERS='{"User-Agent":"Cline/3.5"}')

        self.assertEqual({"User-Agent": "Cline/3.5"}, settings.llm_default_headers)

    def test_invalid_llm_default_headers_fall_back_to_empty(self) -> None:
        from app.config import Settings

        settings = Settings(LLM_DEFAULT_HEADERS="not-json")

        self.assertEqual({}, settings.llm_default_headers)


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


if __name__ == "__main__":
    unittest.main()
