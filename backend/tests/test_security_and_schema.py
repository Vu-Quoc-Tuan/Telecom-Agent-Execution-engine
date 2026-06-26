from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.agent.safety import AgentSafetyGuard, SkillRiskClassifier
from app.common.enums import RiskLevel
from app.observability.redaction import DataRedactor
from app.sandbox.domain_validator import (
    SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT,
    TelecomDomainValidator,
)
from app.sandbox.security_analyzer import AdvancedASTSecurityAnalyzer


class SecurityAnalyzerTests(unittest.TestCase):
    def test_blocks_dangerous_imports_and_dynamic_execution(self) -> None:
        code = "import os\n\ndef read_alarm():\n    return eval('1 + 1')\n"

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("os" in finding for finding in findings))
        self.assertTrue(any("eval" in finding for finding in findings))

    def test_blocks_class_navigation_jailbreak(self) -> None:
        code = "def exploit_skill(ssh_client):\n    return ().__class__.__base__.__subclasses__()\n"

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("__subclasses__" in f or "__class__" in f for f in findings))

    def test_blocks_importlib_and_ctypes(self) -> None:
        for module in ("importlib", "ctypes"):
            code = f"import {module}\n\ndef run(ssh_client):\n    return 1\n"
            is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)
            self.assertFalse(is_clean, module)
            self.assertTrue(any(module in f for f in findings), module)

    def test_blocks_builtins_subscript_bypass(self) -> None:
        code = "def run(ssh_client):\n    return __builtins__['eval']('1+1')\n"

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("__builtins__" in f for f in findings))

    def test_allows_main_guard_dunder_name(self) -> None:
        code = (
            "def get_node_status(ssh_client, node_name: str):\n"
            "    return node_name\n\n"
            "if __name__ == '__main__':\n"
            "    pass\n"
        )

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertTrue(is_clean, findings)

    def test_accepts_connector_injected_telecom_skill(self) -> None:
        code = (
            "def get_alarm_summary(clickhouse_conn, node_name: str):\n"
            "    return clickhouse_conn.query(\n"
            "        'SELECT * FROM alarms WHERE node = {node:String}',\n"
            "        {'node': node_name},\n"
            "    )\n"
        )

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertTrue(is_clean, findings)


class DomainValidatorPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_domain_judge_uses_dedicated_system_prompt(self) -> None:
        class CapturingGateway:
            def __init__(self) -> None:
                self.system_prompt = None

            async def invoke(self, messages, system_prompt=None):
                self.system_prompt = system_prompt
                return SimpleNamespace(
                    content=(
                        '{"domain_score": 0.9, "reason": "telecom workflow", '
                        '"suspicious_points": "None"}'
                    )
                )

        gateway = CapturingGateway()

        result = await TelecomDomainValidator.invoke_llm_domain_judge(
            gateway,
            "check-node-status",
            "Check telecom node status",
            "def run(): return 'ok'",
        )

        self.assertEqual(SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT, gateway.system_prompt)
        self.assertEqual(0.9, result.domain_score)


class SafetyGuardTests(unittest.TestCase):
    def test_rejects_command_chaining_even_when_each_fragment_looks_safe(self) -> None:
        is_safe, reason = AgentSafetyGuard.verify_ssh_command("hostname; whoami")

        self.assertFalse(is_safe)
        self.assertIn("chuỗi", reason.lower())

    def test_masks_credentials_before_prompt_leaves_the_system(self) -> None:
        prompt = "password=secret-value token: abc123"

        sanitized = AgentSafetyGuard.sanitize_input_prompt(prompt)

        self.assertNotIn("secret-value", sanitized)
        self.assertNotIn("abc123", sanitized)

    def test_classifies_mutating_skill_as_dangerous(self) -> None:
        risk = SkillRiskClassifier.classify(
            name="restart_service",
            description="Restart a failed service on a telecom node",
            command="systemctl restart vdt",
        )

        self.assertEqual(RiskLevel.DANGEROUS_ACTION, risk)

    def test_redacts_unquoted_credentials_in_log_text(self) -> None:
        raw = (
            "connect failed password=secret-value token: abc123 api-key=test-key "
            'private_key="quoted-secret"'
        )

        redacted = DataRedactor.redact_text(raw)

        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("test-key", redacted)
        self.assertNotIn("quoted-secret", redacted)
        self.assertIn("password=[REDACTED]", redacted)
        self.assertIn("token: [REDACTED]", redacted)
        self.assertIn("api-key=[REDACTED]", redacted)
        self.assertIn('private_key="[REDACTED]"', redacted)

    def test_redacts_common_private_key_blocks(self) -> None:
        raw = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-body\n-----END OPENSSH PRIVATE KEY-----"

        redacted = DataRedactor.redact_text(raw)

        self.assertNotIn("secret-body", redacted)
        self.assertEqual("[REDACTED PRIVATE KEY]", redacted)

    def test_verify_read_only_sql(self) -> None:
        # Standard select
        is_safe, err = AgentSafetyGuard.verify_read_only_sql("SELECT * FROM station")
        self.assertTrue(is_safe)
        self.assertIsNone(err)

        # Allowed introspection commands
        for cmd in ["DESCRIBE TABLE alarm", "DESC alarm_data.station", "SHOW TABLES", "EXPLAIN SELECT 1"]:
            is_safe, err = AgentSafetyGuard.verify_read_only_sql(cmd)
            self.assertTrue(is_safe, f"Failed on: {cmd}")
            self.assertIsNone(err)

        # Prohibited mutations
        for cmd in ["INSERT INTO station VALUES (1)", "DROP TABLE station", "UPDATE station SET name = 'abc'"]:
            is_safe, err = AgentSafetyGuard.verify_read_only_sql(cmd)
            self.assertFalse(is_safe, f"Allowed prohibited: {cmd}")
            self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
