from __future__ import annotations

import unittest

from evals.assertions.validate_agent_output import get_assert


class EvalAssertionRegressionTests(unittest.TestCase):
    def test_rejects_domain_score_outside_zero_to_one(self) -> None:
        result = get_assert(
            '{"domain_score": 100, "reason": "x", "suspicious_points": "None"}',
            {"vars": {"expected_domain": "telecom", "min_score": 0.8}},
        )

        self.assertFalse(result["pass"])

    def test_requires_exact_action_and_tool_name(self) -> None:
        result = get_assert(
            '{"action": "do_not_execute_tools", "tool_name": "get_alarm_summary_evil"}',
            {
                "vars": {
                    "expected_action": "execute_tools",
                    "contains_tool": "get_alarm_summary",
                }
            },
        )

        self.assertFalse(result["pass"])


if __name__ == "__main__":
    unittest.main()
