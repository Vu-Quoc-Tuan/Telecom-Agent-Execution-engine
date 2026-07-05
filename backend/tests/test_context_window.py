from __future__ import annotations

import unittest
from typing import Any

from app.agent.context_window import compact_messages_if_needed, estimate_context_tokens
from app.llm.schemas import LLMMessage, LLMResponse, MessageRole, NormalizedToolCall


class FakeCompactionGateway:
    def __init__(self, summary: str = "Mục tiêu: kiểm tra lỗi cũ") -> None:
        self.summary = summary
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        return LLMResponse(content=self.summary, provider="test", model="test-compactor")


class FailingCompactionGateway:
    async def invoke(self, messages, **kwargs):
        raise TimeoutError("compactor timed out")


class ContextWindowCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_compact_when_under_threshold(self) -> None:
        messages = [LLMMessage(role=MessageRole.USER, content="check node")]
        gateway = FakeCompactionGateway()

        plan = await compact_messages_if_needed(
            messages,
            llm_gateway=gateway,
            compaction_prompt="compact old messages",
            system_prompt="system",
            tools=[],
            context_window_tokens=10_000,
            trigger_ratio=0.65,
            target_ratio=0.45,
        )

        self.assertFalse(plan.was_compacted)
        self.assertEqual(messages, plan.messages)
        self.assertEqual(
            estimate_context_tokens(messages, system_prompt="system", tools=[]),
            plan.original_tokens,
        )
        self.assertEqual([], gateway.calls)

    async def test_uses_llm_summary_and_keeps_recent_tool_pair_valid(self) -> None:
        old_tool_output = "password=super-secret\n" + ("interface down\n" * 200)
        llm_summary = (
            "Mục tiêu: kiểm tra lỗi cũ\n"
            "Hành động và kết quả:\n"
            "- get_node_health_snapshot phát hiện interface down.\n"
            "api_key=summary-secret"
        )
        gateway = FakeCompactionGateway(llm_summary)
        recent_tool_call = NormalizedToolCall(
            id="call-recent",
            name="ping_node",
            arguments={"node_name": "hanoi-core-01", "count": 3},
        )
        messages = [
            LLMMessage(role=MessageRole.USER, content="kiểm tra lỗi cũ"),
            LLMMessage(
                role=MessageRole.ASSISTANT,
                content=None,
                tool_calls=[
                    NormalizedToolCall(
                        id="call-old",
                        name="get_node_health_snapshot",
                        arguments={"node_name": "site-a", "password": "tool-secret"},
                    )
                ],
            ),
            LLMMessage(role=MessageRole.TOOL, tool_call_id="call-old", content=old_tool_output),
            LLMMessage(role=MessageRole.USER, content="giờ check node mới"),
            LLMMessage(role=MessageRole.ASSISTANT, content=None, tool_calls=[recent_tool_call]),
            LLMMessage(
                role=MessageRole.TOOL, tool_call_id="call-recent", content="load average 0.2"
            ),
        ]

        plan = await compact_messages_if_needed(
            messages,
            llm_gateway=gateway,
            compaction_prompt="compact old messages",
            system_prompt="system",
            tools=[],
            context_window_tokens=1_600,
            trigger_ratio=0.2,
            target_ratio=0.04,
        )

        self.assertTrue(plan.was_compacted)
        self.assertEqual(MessageRole.SYSTEM, plan.messages[0].role)
        self.assertIn("[AUTO-COMPACTED CONTEXT]", plan.messages[0].content)
        self.assertNotIn("summary-secret", plan.messages[0].content)
        self.assertNotEqual(MessageRole.TOOL, plan.messages[1].role)
        self.assertEqual("call-recent", plan.messages[-1].tool_call_id)
        self.assertLess(plan.compacted_tokens, plan.original_tokens)
        self.assertEqual(1, len(gateway.calls))
        self.assertEqual("compact old messages", gateway.calls[0]["system_prompt"])
        self.assertEqual([], gateway.calls[0]["tools"])
        compacted_messages = gateway.calls[0]["messages"]
        compacted_contents = [message.content for message in compacted_messages]
        self.assertNotIn("super-secret", "\n".join(item or "" for item in compacted_contents))
        self.assertNotIn(
            "tool-secret",
            str([tool.arguments for message in compacted_messages for tool in message.tool_calls]),
        )
        self.assertNotIn("load average 0.2", compacted_contents)
        self.assertEqual(0, gateway.calls[0]["options"].temperature)
        self.assertEqual(1200, gateway.calls[0]["options"].max_tokens)

    async def test_tiny_budget_keeps_latest_valid_user_turn_instead_of_full_history(self) -> None:
        prompt_calls = 0

        def load_compaction_prompt() -> str:
            nonlocal prompt_calls
            prompt_calls += 1
            return "compact old messages"

        messages = [
            LLMMessage(role=MessageRole.USER, content="old request " + "x" * 1000),
            LLMMessage(role=MessageRole.ASSISTANT, content="old answer " + "y" * 1000),
            LLMMessage(role=MessageRole.USER, content="latest request"),
        ]

        plan = await compact_messages_if_needed(
            messages,
            llm_gateway=FakeCompactionGateway("Mục tiêu: old request"),
            compaction_prompt=load_compaction_prompt,
            context_window_tokens=20,
            trigger_ratio=0.1,
            target_ratio=0.01,
        )

        self.assertTrue(plan.was_compacted)
        self.assertEqual(MessageRole.SYSTEM, plan.messages[0].role)
        self.assertEqual("latest request", plan.messages[-1].content)
        self.assertLess(len(plan.messages), len(messages) + 1)
        self.assertEqual(1, prompt_calls)

    async def test_empty_llm_summary_falls_back_to_deterministic_compaction(self) -> None:
        messages = [
            LLMMessage(role=MessageRole.USER, content="old request " + "x" * 1000),
            LLMMessage(role=MessageRole.ASSISTANT, content="old answer " + "y" * 1000),
            LLMMessage(role=MessageRole.USER, content="latest request"),
        ]

        plan = await compact_messages_if_needed(
            messages,
            llm_gateway=FakeCompactionGateway("  "),
            compaction_prompt="compact old messages",
            context_window_tokens=20,
            trigger_ratio=0.1,
            target_ratio=0.01,
        )

        self.assertTrue(plan.was_compacted)
        self.assertIn("[AUTO-COMPACTED CONTEXT]", plan.messages[0].content)
        self.assertIn("old request", plan.messages[0].content)
        self.assertEqual("latest request", plan.messages[-1].content)

    async def test_compactor_error_falls_back_without_aborting_the_turn(self) -> None:
        messages = [
            LLMMessage(role=MessageRole.USER, content="old request " + "x" * 1000),
            LLMMessage(role=MessageRole.ASSISTANT, content="old answer " + "y" * 1000),
            LLMMessage(role=MessageRole.USER, content="latest request"),
        ]

        plan = await compact_messages_if_needed(
            messages,
            llm_gateway=FailingCompactionGateway(),
            compaction_prompt="compact old messages",
            context_window_tokens=20,
            trigger_ratio=0.1,
            target_ratio=0.01,
        )

        self.assertTrue(plan.was_compacted)
        self.assertIn("old request", plan.messages[0].content)
        self.assertEqual("latest request", plan.messages[-1].content)

    async def test_overlong_llm_summary_never_makes_context_larger(self) -> None:
        messages = [
            LLMMessage(role=MessageRole.USER, content="old request " + "x" * 1000),
            LLMMessage(role=MessageRole.ASSISTANT, content="old answer " + "y" * 1000),
            LLMMessage(role=MessageRole.USER, content="latest request"),
        ]

        plan = await compact_messages_if_needed(
            messages,
            llm_gateway=FakeCompactionGateway("oversized " * 20_000),
            compaction_prompt="compact old messages",
            context_window_tokens=800,
            trigger_ratio=0.5,
            target_ratio=0.25,
        )

        self.assertTrue(plan.was_compacted)
        self.assertLess(plan.compacted_tokens, plan.original_tokens)
        self.assertEqual("latest request", plan.messages[-1].content)


if __name__ == "__main__":
    unittest.main()
