from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.safety import AgentSafetyGuard
from app.common.enums import ExecutionMode
from app.common.exceptions import SafetyViolationError
from app.observability.redaction import DataRedactor
from app.sandbox.domain_validator import (
    SKILL_DOMAIN_JUDGE_FALLBACK_PROMPT,
    SKILL_DOMAIN_JUDGE_PROMPT_NAME,
    SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT,
    TelecomDomainValidator,
)
from app.sandbox.security_analyzer import AdvancedASTSecurityAnalyzer


class SecurityAnalyzerTests(unittest.TestCase):
    def test_allows_dev_connectivity_imports_but_blocks_dynamic_execution(self) -> None:
        code = "import os\n\ndef read_alarm():\n    return eval('1 + 1')\n"

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertFalse(any("os" in finding for finding in findings))
        self.assertTrue(any("eval" in finding for finding in findings))

    def test_allows_reviewable_dev_connection_modules(self) -> None:
        code = (
            "import os\n"
            "import ssl\n"
            "import requests\n"
            "import httpx\n"
            "import paramiko\n"
            "from urllib.parse import quote_plus\n"
            "def run():\n"
            "    return quote_plus(os.environ.get('NODE', ''))\n"
        )

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertTrue(is_clean, findings)

    def test_blocks_sys_modules_dynamic_import_bypass(self) -> None:
        code = (
            "import sys\n"
            "def run():\n"
            "    return sys.modules['subprocess'].run(['id'])\n"
        )

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("sys" in finding for finding in findings), findings)

    def test_sample_autoremediation_skill_rejects_unknown_ssh_host_keys(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[2]
            / "Agent_skill/node-health-autoremediate/scripts/health_action.py"
        )
        source = script_path.read_text(encoding="utf-8")

        self.assertIn("paramiko.RejectPolicy()", source)
        self.assertIn("load_host_keys", source)
        self.assertNotIn("paramiko.AutoAddPolicy()", source)

    def test_blocks_subprocess_and_os_shell_execution(self) -> None:
        code = (
            "import os\n"
            "import subprocess\n"
            "def run():\n"
            "    os.system('id')\n"
            "    return subprocess.run(['id'])\n"
        )

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("subprocess" in finding for finding in findings))
        self.assertTrue(any("os.system" in finding for finding in findings))

    def test_blocks_class_navigation_jailbreak(self) -> None:
        code = "def exploit_skill(ssh_client):\n    return ().__class__.__base__.__subclasses__()\n"

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("__subclasses__" in f or "__class__" in f for f in findings))

    def test_blocks_importlib_and_ctypes(self) -> None:
        for module in ("importlib", "ctypes", "pydoc"):
            code = f"import {module}\n\ndef run(ssh_client):\n    return 1\n"
            is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)
            self.assertFalse(is_clean, module)
            self.assertTrue(any(module in f for f in findings), module)

    def test_blocks_pydoc_locate_dynamic_import_bypass(self) -> None:
        code = (
            "import pydoc\n\n"
            "os = pydoc.locate('os')\n"
            "subprocess = pydoc.locate('subprocess')\n"
            "paramiko = pydoc.locate('paramiko')\n"
        )

        is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)

        self.assertFalse(is_clean)
        self.assertTrue(any("pydoc" in finding for finding in findings), findings)

    def test_blocks_background_execution_primitives(self) -> None:
        for module in (
            "asyncio",
            "concurrent.futures",
            "multiprocessing",
            "threading",
            "sched",
            "atexit",
        ):
            code = f"import {module}\n\ndef run():\n    return 1\n"
            is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(code)
            self.assertFalse(is_clean, module)
            self.assertTrue(
                any(module.split(".")[0] in finding for finding in findings),
                module,
            )

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

    def test_allows_read_only_open_of_workspace_args(self) -> None:
        for snippet in (
            "import json\nargs = json.load(open('args.json'))\n",
            "args = open('args.json', 'r').read()\n",
            "args = open('data/lookup.csv', mode='rt').read()\n",
            "args = open('args.json', 'rb').read()\n",
        ):
            is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(snippet)
            self.assertTrue(is_clean, f"Rejected safe open: {snippet} -> {findings}")

    def test_blocks_unsafe_open_calls(self) -> None:
        unsafe_snippets = (
            "open('/etc/hosts')\n",  # absolute path
            "open('../secret.txt')\n",  # path traversal
            "open('out.txt', 'w')\n",  # write mode
            "open('out.txt', 'a')\n",  # append mode
            "open('out.txt', 'r+')\n",  # read/write
            "p = 'args.json'\nopen(p)\n",  # non-literal path
            "open('args.json', mode=some_var)\n",  # non-literal mode
            "open()\n",  # no args
        )
        for snippet in unsafe_snippets:
            is_clean, findings = AdvancedASTSecurityAnalyzer.analyze_source_code(snippet)
            self.assertFalse(is_clean, f"Allowed unsafe open: {snippet}")
            self.assertTrue(any("open()" in f for f in findings), f"{snippet} -> {findings}")


class DomainValidatorPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_domain_judge_compiles_managed_prompt(self) -> None:
        class ManagedPrompt:
            def compile(self, **variables):
                return (
                    f"managed:{variables['name']}|{variables['description']}|"
                    f"{variables['code_text']}"
                )

        class CapturingGateway:
            def __init__(self) -> None:
                self.user_prompt = None

            async def invoke(self, messages, system_prompt=None):
                self.user_prompt = messages[0].content
                return SimpleNamespace(
                    content=(
                        '{"domain_score": 0.9, "reason": "telecom workflow", '
                        '"suspicious_points": "None"}'
                    )
                )

        gateway = CapturingGateway()
        with patch(
            "app.sandbox.domain_validator.telemetry_tracker.get_prompt",
            return_value=ManagedPrompt(),
        ) as get_prompt:
            await TelecomDomainValidator.invoke_llm_domain_judge(
                gateway,
                "check-node-status",
                "Check telecom node status",
                "def run(): return 'ok'",
            )

        get_prompt.assert_called_once_with(
            SKILL_DOMAIN_JUDGE_PROMPT_NAME,
            fallback_text=SKILL_DOMAIN_JUDGE_FALLBACK_PROMPT,
        )
        self.assertEqual(
            "managed:check-node-status|Check telecom node status|def run(): return 'ok'",
            gateway.user_prompt,
        )

    async def test_llm_domain_judge_uses_short_local_fallback_without_langfuse(self) -> None:
        class CapturingGateway:
            def __init__(self) -> None:
                self.user_prompt = None

            async def invoke(self, messages, system_prompt=None):
                self.user_prompt = messages[0].content
                return SimpleNamespace(
                    content=(
                        '{"domain_score": 0.9, "reason": "telecom workflow", '
                        '"suspicious_points": "None"}'
                    )
                )

        gateway = CapturingGateway()
        with patch(
            "app.sandbox.domain_validator.telemetry_tracker.get_prompt",
            return_value=None,
        ):
            await TelecomDomainValidator.invoke_llm_domain_judge(
                gateway,
                "check-node-status",
                "Check telecom node status",
                "def run(): return 'ok'",
            )

        self.assertIn("check-node-status", gateway.user_prompt)
        self.assertIn("Check telecom node status", gateway.user_prompt)
        self.assertIn("def run(): return 'ok'", gateway.user_prompt)
        self.assertNotIn("hack game", gateway.user_prompt)

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

    async def test_llm_domain_judge_accepts_common_json_variants(self) -> None:
        class VariantGateway:
            async def invoke(self, messages, system_prompt=None):
                return SimpleNamespace(
                    content=(
                        "```json\n"
                        '{"result": {"score": "85%", "explanation": "telecom alarm enrichment", '
                        '"suspicious": ["None"]}}\n'
                        "```"
                    )
                )

        result = await TelecomDomainValidator.invoke_llm_domain_judge(
            VariantGateway(),
            "no-alarm-enrichment",
            "Enrich no alarm telecom cases",
            "def run(): return 'ok'",
        )

        self.assertEqual(0.85, result.domain_score)
        self.assertEqual("telecom alarm enrichment", result.reason)
        self.assertEqual("None", result.suspicious_points)

    async def test_llm_domain_judge_failure_includes_redacted_preview(self) -> None:
        class BadGateway:
            async def invoke(self, messages, system_prompt=None):
                return SimpleNamespace(content="password=secret-value I cannot return JSON")

        result = await TelecomDomainValidator.invoke_llm_domain_judge(
            BadGateway(),
            "no-alarm-enrichment",
            "Enrich no alarm telecom cases",
            "def run(): return 'ok'",
        )

        self.assertEqual(0.0, result.domain_score)
        self.assertIn("Raw preview", result.reason)
        self.assertIn("password=[REDACTED]", result.reason)
        self.assertNotIn("secret-value", result.reason)


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

    def test_classify_ssh_command_edge_cases(self) -> None:
        # Empty command raises SafetyViolationError
        with self.assertRaises(SafetyViolationError):
            AgentSafetyGuard.classify_ssh_command("")

        # Chained command raises SafetyViolationError
        with self.assertRaises(SafetyViolationError):
            AgentSafetyGuard.classify_ssh_command("hostname && whoami")

        # Read-only command returns ExecutionMode.AUTO_EXECUTE
        self.assertEqual(
            ExecutionMode.AUTO_EXECUTE, AgentSafetyGuard.classify_ssh_command("hostname")
        )
        self.assertEqual(ExecutionMode.AUTO_EXECUTE, AgentSafetyGuard.classify_ssh_command("df -h"))

        # Mutation/sensitive command returns ExecutionMode.REQUIRE_APPROVAL
        self.assertEqual(
            ExecutionMode.REQUIRE_APPROVAL,
            AgentSafetyGuard.classify_ssh_command("systemctl restart nginx"),
        )

    def test_verify_ssh_command_behavior(self) -> None:
        # Auto execute command requires 0 confirmations and is safe
        is_safe, err = AgentSafetyGuard.verify_ssh_command("hostname", approval_confirmations=0)
        self.assertTrue(is_safe)
        self.assertIsNone(err)

        # Standard mutation command requires at least 1 confirmation
        is_safe, err = AgentSafetyGuard.verify_ssh_command(
            "systemctl restart nginx", approval_confirmations=0
        )
        self.assertFalse(is_safe)
        self.assertIn("xác nhận một lần", err)

        is_safe, err = AgentSafetyGuard.verify_ssh_command(
            "systemctl restart nginx", approval_confirmations=1
        )
        self.assertTrue(is_safe)
        self.assertIsNone(err)

        # Critical command fails validation completely, even with confirmations
        is_safe, err = AgentSafetyGuard.verify_ssh_command("rm -rf /", approval_confirmations=1)
        self.assertFalse(is_safe)
        self.assertIn("cấm thực thi", err)

        is_safe, err = AgentSafetyGuard.verify_ssh_command("rm -rf /", approval_confirmations=2)
        self.assertFalse(is_safe)
        self.assertIn("cấm thực thi", err)

    def test_truncate_output(self) -> None:
        text = "a" * 20000
        truncated, was_truncated = AgentSafetyGuard.truncate_output(text, max_characters=1000)
        self.assertTrue(was_truncated)
        self.assertEqual(
            1000
            + len(
                "\n\n... [HỆ THỐNG CẮT GIẢM: Nội dung log quá dài đã bị cắt bớt để bảo vệ an toàn bộ nhớ Agent] ..."
            ),
            len(truncated),
        )

        # Not truncated
        short_text = "hello"
        truncated, was_truncated = AgentSafetyGuard.truncate_output(short_text, max_characters=1000)
        self.assertFalse(was_truncated)
        self.assertEqual("hello", truncated)


if __name__ == "__main__":
    unittest.main()
