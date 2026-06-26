from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, patch

from app.agent.builtin_tools import (
    build_builtin_tool_definitions,
    classify_builtin_risk,
    execute_builtin_tool,
)
from app.common.enums import RiskLevel
from app.common.exceptions import SkillRuntimeError


class FakeSkill:
    def __init__(
        self,
        *,
        name: str,
        skill_md: str,
        bundled_files: dict | None = None,
        status: str = "ready",
    ):
        self.name = name
        self.skill_md = skill_md
        self.bundled_files = bundled_files or {}
        self.status = status


class FakeSkillRepository:
    def __init__(self, skill: FakeSkill):
        self.skill = skill

    def get_skill_by_name(self, db, name: str):
        return self.skill if self.skill.name == name else None


class BuiltinToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    def test_skill_tools_are_registered_only_when_ready_skills_exist(self) -> None:
        without_skills = build_builtin_tool_definitions([])
        self.assertNotIn("load_skill", {tool.name for tool in without_skills})
        self.assertNotIn("read_skill_file", {tool.name for tool in without_skills})

        ready_skills = [types.SimpleNamespace(name="check-kpis")]
        with_skills = build_builtin_tool_definitions(ready_skills)
        load_skill = next(tool for tool in with_skills if tool.name == "load_skill")
        self.assertEqual(
            ["check-kpis"],
            load_skill.input_schema["properties"]["skill_name"]["enum"],
        )

    async def test_load_skill_returns_markdown_body(self) -> None:
        from app.database.repositories.skills import SkillRepository

        skill = FakeSkill(
            name="check_kpis",
            skill_md="# check_kpis\nInstructions body",
        )
        # Patch the repository get_skill_by_name
        orig_get = SkillRepository.get_skill_by_name
        SkillRepository.get_skill_by_name = MagicMock(return_value=skill)

        try:
            output, truncated = await execute_builtin_tool(
                tool_name="load_skill",
                arguments={"skill_name": "check_kpis"},
                db=object(),
            )
            self.assertFalse(truncated)
            self.assertIn('<skill_content name="check_kpis">', output)
            self.assertIn("# check_kpis\nInstructions body", output)
            self.assertIn("<skill_resources>", output)
        finally:
            SkillRepository.get_skill_by_name = orig_get

    async def test_read_skill_file_returns_bundled_file_content(self) -> None:
        from app.database.repositories.skills import SkillRepository

        skill = FakeSkill(
            name="check_kpis",
            skill_md="# check_kpis",
            bundled_files={
                "references/checklist.txt": {
                    "encoding": "utf-8",
                    "content": "1. Check CPU\n2. Check Memory",
                    "media_type": "text/plain",
                    "size": 34,
                }
            },
        )
        orig_get = SkillRepository.get_skill_by_name
        SkillRepository.get_skill_by_name = MagicMock(return_value=skill)

        try:
            output, truncated = await execute_builtin_tool(
                tool_name="read_skill_file",
                arguments={
                    "skill_name": "check_kpis",
                    "file_path": "references/checklist.txt",
                },
                db=object(),
            )
            self.assertFalse(truncated)
            self.assertEqual(output, "1. Check CPU\n2. Check Memory")
        finally:
            SkillRepository.get_skill_by_name = orig_get

    async def test_ssh_command_raises_if_node_not_allowed(self) -> None:
        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a,site-b",
            SSH_HOST="",
            SSH_PORT=22,
            SSH_USER="noc",
            SSH_PASSWORD="pwd",
            SSH_TIMEOUT_SECONDS=5,
        )

        with self.assertRaises(SkillRuntimeError) as ctx:
            await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "unauthorized-node", "command": "hostname"},
                db=object(),
                settings=settings,
            )

        self.assertIn("SSH_ALLOWED_NODES", ctx.exception.message)

    async def test_ssh_uses_requested_node_when_allowlist_is_configured(self) -> None:
        class FakeSSHConnector:
            instances = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                FakeSSHConnector.instances.append(self)

            async def execute_command(self, command: str):
                self.command = command
                return "ok", ""

            def close(self):
                self.closed = True

        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a,site-b",
            SSH_HOST="global-host.example.test",
            SSH_PORT=22,
            SSH_USER="noc",
            SSH_PASSWORD="pwd",
            SSH_TIMEOUT_SECONDS=5,
            SSH_KNOWN_HOSTS="",
            SSH_AUTO_ADD_HOST_KEYS=False,
        )

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-b", "command": "hostname"},
                db=object(),
                settings=settings,
            )

        self.assertEqual("ok", output)
        self.assertFalse(truncated)
        self.assertEqual("site-b", FakeSSHConnector.instances[0].kwargs["host"])

    async def test_ssh_can_resolve_logical_node_to_configured_host(self) -> None:
        class FakeSSHConnector:
            instances = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                FakeSSHConnector.instances.append(self)

            async def execute_command(self, command: str):
                return "ok", ""

            def close(self):
                pass

        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a,site-b",
            SSH_NODE_HOST_MAP="site-a=10.0.0.11, site-b=node-b.internal",
            SSH_HOST="global-host.example.test",
            SSH_PORT=22,
            SSH_USER="noc",
            SSH_PASSWORD="pwd",
            SSH_TIMEOUT_SECONDS=5,
            SSH_KNOWN_HOSTS="",
            SSH_AUTO_ADD_HOST_KEYS=False,
        )

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-b", "command": "hostname"},
                db=object(),
                settings=settings,
            )

        self.assertEqual("ok", output)
        self.assertFalse(truncated)
        self.assertEqual("node-b.internal", FakeSSHConnector.instances[0].kwargs["host"])

    async def test_ssh_tool_output_is_redacted_before_returning_to_llm(self) -> None:
        class FakeSSHConnector:
            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command: str):
                return "password=super-secret token: abc123", ""

            def close(self):
                pass

        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_HOST="",
            SSH_PORT=22,
            SSH_USER="noc",
            SSH_PASSWORD="pwd",
            SSH_TIMEOUT_SECONDS=5,
            SSH_KNOWN_HOSTS="",
            SSH_AUTO_ADD_HOST_KEYS=False,
        )

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-a", "command": "hostname"},
                db=object(),
                settings=settings,
            )

        self.assertFalse(truncated)
        self.assertNotIn("super-secret", output)
        self.assertNotIn("abc123", output)
        self.assertIn("[REDACTED]", output)

    async def test_ssh_strips_safe_output_limit_pipe_before_execution(self) -> None:
        class FakeSSHConnector:
            command = None

            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command: str):
                FakeSSHConnector.command = command
                return "ok", ""

            def close(self):
                pass

        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_HOST="",
            SSH_PORT=22,
            SSH_USER="noc",
            SSH_PASSWORD="pwd",
            SSH_TIMEOUT_SECONDS=5,
            SSH_KNOWN_HOSTS="",
            SSH_AUTO_ADD_HOST_KEYS=False,
        )
        command = "ps -eo pid,ppid,comm,%mem,%cpu --sort=-%cpu | head -n 10"

        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": command},
        )

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-a", "command": command},
                db=object(),
                settings=settings,
            )

        self.assertEqual(RiskLevel.READ_ONLY.value, risk)
        self.assertEqual("ps -eo pid,ppid,comm,%mem,%cpu --sort=-%cpu", FakeSSHConnector.command)
        self.assertEqual("ok", output)
        self.assertFalse(truncated)

    def test_classify_builtin_risk_checks_dangerous_terms(self) -> None:
        # Safe command
        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "show configuration"},
        )
        self.assertEqual(RiskLevel.READ_ONLY.value, risk)

        # Dangerous command
        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "systemctl restart telco_service"},
        )
        self.assertEqual(RiskLevel.DANGEROUS_ACTION.value, risk)

    def test_ssh_state_changes_require_approval(self) -> None:
        for command in ("touch /tmp/pwn", "sed -i s/a/b/ /etc/app.conf", "mkdir /tmp/work"):
            with self.subTest(command=command):
                risk = classify_builtin_risk(
                    tool_name="run_ssh_command",
                    arguments={"node_name": "site-a", "command": command},
                )
                self.assertEqual(RiskLevel.DANGEROUS_ACTION.value, risk)

    def test_ssh_critical_commands_are_prohibited(self) -> None:
        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "rm -rf /"},
        )
        self.assertEqual(RiskLevel.PROHIBITED.value, risk)

    def test_ssh_sensitive_file_reads_are_prohibited(self) -> None:
        for command in (
            "cat /etc/shadow",
            "cat /etc/../etc/shadow",
            "tail ~/.ssh/id_rsa",
            "tail ~/.ssh/../.ssh/id_rsa",
            "grep token .env",
            "grep token ./.env",
            "head /proc/self/environ",
        ):
            with self.subTest(command=command):
                risk = classify_builtin_risk(
                    tool_name="run_ssh_command",
                    arguments={"node_name": "site-a", "command": command},
                )
                self.assertEqual(RiskLevel.PROHIBITED.value, risk)

    def test_clickhouse_mutations_are_prohibited(self) -> None:
        for sql in (
            "DROP TABLE alarms",
            "ALTER TABLE alarms DELETE WHERE id = 1",
            "SELECT 1; DROP TABLE alarms",
        ):
            with self.subTest(sql=sql):
                risk = classify_builtin_risk(
                    tool_name="query_clickhouse",
                    arguments={"sql": sql},
                )
                self.assertEqual(RiskLevel.PROHIBITED.value, risk)

    def test_clickhouse_select_is_read_only(self) -> None:
        risk = classify_builtin_risk(
            tool_name="query_clickhouse",
            arguments={
                "sql": "WITH 5 AS threshold SELECT * FROM alarms WHERE severity > threshold"
            },
        )
        self.assertEqual(RiskLevel.READ_ONLY.value, risk)

    def test_clickhouse_system_database_select_is_read_only(self) -> None:
        risk = classify_builtin_risk(
            tool_name="query_clickhouse",
            arguments={"sql": "SELECT count() FROM system.tables"},
        )
        self.assertEqual(RiskLevel.READ_ONLY.value, risk)

    def test_postgres_mutations_are_prohibited_before_connector_execution(self) -> None:
        for sql in (
            "DROP TABLE alarms",
            "UPDATE inventory SET status = 'down'",
            "SELECT 1; DELETE FROM inventory",
        ):
            with self.subTest(sql=sql):
                risk = classify_builtin_risk(
                    tool_name="query_postgres",
                    arguments={"sql": sql},
                )
                self.assertEqual(RiskLevel.PROHIBITED.value, risk)

    def test_postgres_select_is_read_only(self) -> None:
        risk = classify_builtin_risk(
            tool_name="query_postgres",
            arguments={"sql": "SELECT * FROM inventory LIMIT 1"},
        )
        self.assertEqual(RiskLevel.READ_ONLY.value, risk)


class ExternalPostgresConnectorTests(unittest.TestCase):
    def test_connector_is_built_from_external_settings_not_app_session(self) -> None:
        from app.connectors.postgres import TelcoPostgresConnector

        connector = TelcoPostgresConnector(
            host="db.example.test",
            port=5432,
            username="readonly",
            password="",
            database="telecom",
            read_only=True,
        )

        self.assertEqual("db.example.test", connector.host)
        self.assertEqual("", connector.password)
        self.assertTrue(connector.read_only)


class ClickHouseConnectorSafetyTests(unittest.TestCase):
    def test_query_enforces_server_read_only_and_row_limit(self) -> None:
        from app.connectors.clickhouse import TelcoClickHouseConnector

        client = MagicMock()
        client.query.return_value = types.SimpleNamespace(
            column_names=["value"],
            result_rows=[(1,), (2,)],
        )
        connector = TelcoClickHouseConnector(
            host="clickhouse.example.test",
            max_result_rows=2,
        )
        connector._client = client

        rows = connector._sync_query("SELECT value FROM metrics")

        self.assertEqual([{"value": 1}, {"value": 2}], rows)
        client.query.assert_called_once_with(
            "SELECT value FROM metrics",
            parameters=None,
            settings={
                "readonly": 2,
                "max_result_rows": 2,
                "result_overflow_mode": "break",
            },
        )


if __name__ == "__main__":
    unittest.main()
