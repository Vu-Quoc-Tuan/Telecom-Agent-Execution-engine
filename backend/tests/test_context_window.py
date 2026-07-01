from __future__ import annotations

import unittest

from app.agent.context_window import compact_messages_if_needed, estimate_context_tokens
from app.llm.schemas import LLMMessage, MessageRole, NormalizedToolCall


class ContextWindowCompactionTests(unittest.TestCase):
    def test_does_not_compact_when_under_threshold(self) -> None:
        messages = [LLMMessage(role=MessageRole.USER, content="check node")]

        plan = compact_messages_if_needed(
            messages,
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

    def test_compacts_old_tool_outputs_and_keeps_recent_tool_pair_valid(self) -> None:
        old_tool_output = "password=super-secret\n" + ("interface down\n" * 200)
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
                        arguments={"node_name": "site-a"},
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

        plan = compact_messages_if_needed(
            messages,
            system_prompt="system",
            tools=[],
            context_window_tokens=1_600,
            trigger_ratio=0.2,
            target_ratio=0.04,
        )

        self.assertTrue(plan.was_compacted)
        self.assertEqual(MessageRole.SYSTEM, plan.messages[0].role)
        self.assertIn("AUTO-COMPACTED CONTEXT", plan.messages[0].content or "")
        self.assertIn("get_node_health_snapshot", plan.messages[0].content or "")
        self.assertNotIn("super-secret", plan.messages[0].content or "")
        self.assertNotEqual(MessageRole.TOOL, plan.messages[1].role)
        self.assertEqual("call-recent", plan.messages[-1].tool_call_id)
        self.assertLess(plan.compacted_tokens, plan.original_tokens)

    def test_tiny_budget_keeps_latest_valid_user_turn_instead_of_full_history(self) -> None:
        messages = [
            LLMMessage(role=MessageRole.USER, content="old request " + "x" * 1000),
            LLMMessage(role=MessageRole.ASSISTANT, content="old answer " + "y" * 1000),
            LLMMessage(role=MessageRole.USER, content="latest request"),
        ]

        plan = compact_messages_if_needed(
            messages,
            context_window_tokens=20,
            trigger_ratio=0.1,
            target_ratio=0.01,
        )

        self.assertTrue(plan.was_compacted)
        self.assertEqual(MessageRole.SYSTEM, plan.messages[0].role)
        self.assertEqual("latest request", plan.messages[-1].content)
        self.assertLess(len(plan.messages), len(messages) + 1)


if __name__ == "__main__":
    unittest.main()
