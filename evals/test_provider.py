from __future__ import annotations

import json
import unittest

from evals.providers.backend_policy import call_api


class LocalBackendProviderTests(unittest.TestCase):
    def test_dangerous_risk_uses_real_backend_routing_policy(self) -> None:
        result = call_api(
            "routing",
            {},
            {
                "vars": {
                    "case_type": "routing",
                    "tool_name": "restart_service",
                    "risk_level": "dangerous_action",
                }
            },
        )

        self.assertEqual("suspend_for_human", json.loads(result["output"])["action"])

    def test_routing_derives_builtin_risk_from_tool_arguments(self) -> None:
        result = call_api(
            "routing",
            {},
            {
                "vars": {
                    "case_type": "routing",
                    "tool_name": "ping_node",
                    "arguments": {"node_name": "site-a", "count": 3},
                }
            },
        )

        self.assertEqual("execute_tools", json.loads(result["output"])["action"])

    def test_routing_fails_removed_raw_proposal_tool(self) -> None:
        result = call_api(
            "routing",
            {},
            {
                "vars": {
                    "case_type": "routing",
                    "tool_name": "query_clickhouse",
                    "arguments": {"sql": "DROP TABLE alarms"},
                }
            },
        )

        self.assertEqual("fail", json.loads(result["output"])["action"])


if __name__ == "__main__":
    unittest.main()
