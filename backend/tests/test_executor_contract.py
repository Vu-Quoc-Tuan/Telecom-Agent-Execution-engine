from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, patch

from app.agent.builtin_tools import (
    BUILTIN_TOOL_NAMES,
    build_builtin_tool_definitions,
    classify_builtin_risk,
    execute_builtin_tool,
    list_backend_owned_capabilities,
    required_approval_confirmations,
)
from app.common.enums import ExecutionMode
from app.common.exceptions import ConnectorExecutionError, SafetyViolationError, SkillRuntimeError


class FakeSkill:
    def __init__(
        self,
        *,
        name: str,
        skill_md: str,
        bundled_files: dict | None = None,
        script_manifest: dict | None = None,
        status: str = "ready",
    ):
        self.name = name
        self.skill_md = skill_md
        self.bundled_files = bundled_files or {}
        self.script_manifest = script_manifest or {}
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
        self.assertNotIn("execute_python_in_sandbox", {tool.name for tool in without_skills})

        ready_skills = [types.SimpleNamespace(name="check-kpis")]
        with_skills = build_builtin_tool_definitions(ready_skills)
        load_skill = next(tool for tool in with_skills if tool.name == "load_skill")
        self.assertEqual(
            ["check-kpis"],
            load_skill.input_schema["properties"]["skill_name"]["enum"],
        )
        self.assertNotIn("execute_python_in_sandbox", {tool.name for tool in with_skills})

    def test_backend_owned_capabilities_are_auto_executable_and_described(self) -> None:
        tools = build_builtin_tool_definitions(
            [],
            settings=types.SimpleNamespace(
                CLICKHOUSE_HOST="clickhouse.example.test",
                EXTERNAL_POSTGRES_HOST="postgres.example.test",
                SSH_ALLOWED_NODES="site-a",
                SSH_HOST="",
            ),
        )
        tool_names = {tool.name for tool in tools}
        self.assertIn("get_site_alarm_summary", tool_names)
        self.assertIn("get_site_kpi_snapshot", tool_names)
        self.assertIn("get_site_inventory", tool_names)
        self.assertIn("get_node_health_snapshot", tool_names)
        self.assertEqual(
            ExecutionMode.AUTO_EXECUTE.value,
            classify_builtin_risk(
                "get_site_alarm_summary",
                {"site_id": "site-a", "window_minutes": 15, "limit": 20},
            ),
        )
        summaries = list_backend_owned_capabilities(
            types.SimpleNamespace(
                CLICKHOUSE_HOST="clickhouse.example.test",
                EXTERNAL_POSTGRES_HOST="",
                SSH_ALLOWED_NODES="",
                SSH_HOST="",
            )
        )
        self.assertEqual(
            ["get_active_alarms", "get_site_alarm_summary", "get_site_kpi_snapshot"],
            [item["name"] for item in summaries],
        )

    async def test_free_form_sandbox_tool_is_fully_removed(self) -> None:
        self.assertNotIn("execute_python_in_sandbox", BUILTIN_TOOL_NAMES)
        with self.assertRaises(SkillRuntimeError):
            await execute_builtin_tool(
                tool_name="execute_python_in_sandbox",
                arguments={"code": "print('hi')"},
                db=object(),
            )

    async def test_get_site_alarm_summary_uses_backend_owned_clickhouse_template(self) -> None:
        class FakeClickHouseConnector:
            sql = None
            params = None

            def __init__(self, **kwargs):
                pass

            async def query(self, sql, params=None):
                FakeClickHouseConnector.sql = sql
                FakeClickHouseConnector.params = params
                return [{"severity": "critical", "alarm_count": 2}]

            def close(self):
                pass

        with patch("app.agent.builtin_tools.TelcoClickHouseConnector", FakeClickHouseConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="get_site_alarm_summary",
                arguments={"site_id": "site-a", "window_minutes": 15, "limit": 20},
                db=object(),
                settings=types.SimpleNamespace(
                    CLICKHOUSE_HOST="clickhouse.example.test",
                    CLICKHOUSE_PORT=8123,
                    CLICKHOUSE_USER="operator",
                    CLICKHOUSE_PASSWORD="secret",
                    CLICKHOUSE_DATABASE="alarm_data",
                    EXTERNAL_CONNECTOR_TIMEOUT_SECONDS=5,
                    QUERY_MAX_RESULT_ROWS=100,
                ),
            )

        self.assertIn('"alarm_count": 2', output)
        self.assertFalse(truncated)
        self.assertIn("FROM alarms", FakeClickHouseConnector.sql)
        self.assertEqual(
            {"site_id": "site-a", "window_minutes": 15, "limit": 20},
            FakeClickHouseConnector.params,
        )

    async def test_get_node_health_snapshot_runs_only_fixed_read_only_commands(self) -> None:
        class FakeSSHConnector:
            commands = []

            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command, *, approval_confirmations=0):
                FakeSSHConnector.commands.append((command, approval_confirmations))
                return f"{command}: ok", ""

            def close(self):
                pass

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="get_node_health_snapshot",
                arguments={"node_name": "site-a"},
                db=object(),
                settings=types.SimpleNamespace(
                    SSH_ALLOWED_NODES="site-a",
                    SSH_NODE_HOST_MAP="site-a=10.0.0.11",
                    SSH_HOST="",
                    SSH_PORT=22,
                    SSH_USER="noc",
                    SSH_PASSWORD="pwd",
                    SSH_TIMEOUT_SECONDS=5,
                    SSH_KNOWN_HOSTS="",
                    SSH_AUTO_ADD_HOST_KEYS=False,
                ),
            )

        self.assertIn("uptime", output)
        self.assertFalse(truncated)
        self.assertEqual(
            [
                ("hostname", 0),
                ("uptime", 0),
                ("free -m", 0),
                ("df -h", 0),
            ],
            FakeSSHConnector.commands,
        )

    async def test_get_active_alarms_uses_fixed_template_and_optional_severity(self) -> None:
        class FakeClickHouseConnector:
            sql = None
            params = None

            def __init__(self, **kwargs):
                pass

            async def query(self, sql, params=None):
                FakeClickHouseConnector.sql = sql
                FakeClickHouseConnector.params = params
                return [{"alarm_id": "a1", "severity": "critical"}]

            def close(self):
                pass

        ch_settings = types.SimpleNamespace(
            CLICKHOUSE_HOST="clickhouse.example.test",
            CLICKHOUSE_PORT=8123,
            CLICKHOUSE_USER="operator",
            CLICKHOUSE_PASSWORD="secret",
            CLICKHOUSE_DATABASE="alarm_data",
            EXTERNAL_CONNECTOR_TIMEOUT_SECONDS=5,
            QUERY_MAX_RESULT_ROWS=100,
        )

        with patch("app.agent.builtin_tools.TelcoClickHouseConnector", FakeClickHouseConnector):
            output, _ = await execute_builtin_tool(
                tool_name="get_active_alarms",
                arguments={"window_minutes": 30, "limit": 50},
                db=object(),
                settings=ch_settings,
            )
        self.assertIn('"alarm_id": "a1"', output)
        self.assertIn("time_solved IS NULL", FakeClickHouseConnector.sql)
        self.assertNotIn("severity =", FakeClickHouseConnector.sql)
        self.assertEqual(
            {"window_minutes": 30, "limit": 50},
            FakeClickHouseConnector.params,
        )

        with patch("app.agent.builtin_tools.TelcoClickHouseConnector", FakeClickHouseConnector):
            await execute_builtin_tool(
                tool_name="get_active_alarms",
                arguments={"window_minutes": 30, "limit": 50, "severity": "critical"},
                db=object(),
                settings=ch_settings,
            )
        self.assertIn("severity = {severity:String}", FakeClickHouseConnector.sql)
        self.assertEqual(
            {"window_minutes": 30, "limit": 50, "severity": "critical"},
            FakeClickHouseConnector.params,
        )
        self.assertEqual(
            ExecutionMode.AUTO_EXECUTE.value,
            classify_builtin_risk("get_active_alarms", {"window_minutes": 30, "limit": 50}),
        )

    async def test_ping_node_runs_fixed_icmp_command(self) -> None:
        class FakeSSHConnector:
            commands = []

            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command, *, approval_confirmations=0):
                FakeSSHConnector.commands.append((command, approval_confirmations))
                return f"{command}: 0% packet loss", ""

            def close(self):
                pass

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, _ = await execute_builtin_tool(
                tool_name="ping_node",
                arguments={"node_name": "site-a", "count": 3},
                db=object(),
                settings=types.SimpleNamespace(
                    SSH_ALLOWED_NODES="site-a",
                    SSH_NODE_HOST_MAP="site-a=10.0.0.11",
                    SSH_HOST="",
                    SSH_PORT=22,
                    SSH_USER="noc",
                    SSH_PASSWORD="pwd",
                    SSH_TIMEOUT_SECONDS=5,
                    SSH_KNOWN_HOSTS="",
                    SSH_AUTO_ADD_HOST_KEYS=False,
                ),
            )

        self.assertIn("packet loss", output)
        self.assertEqual([("ping -c 3 -w 8 10.0.0.11", 0)], FakeSSHConnector.commands)
        self.assertEqual(
            ExecutionMode.AUTO_EXECUTE.value,
            classify_builtin_risk("ping_node", {"node_name": "site-a", "count": 3}),
        )

    def test_tool_argument_validator_covers_required_extra_type_and_enum_edges(self) -> None:
        from app.agent.tool_validation import validate_tool_call_arguments

        tools = build_builtin_tool_definitions([types.SimpleNamespace(name="check-kpis")])

        validate_tool_call_arguments(
            tool_name="query_clickhouse",
            arguments={"sql": "SELECT 1"},
            tools=tools,
        )

        with self.assertRaises(SkillRuntimeError) as missing_ctx:
            validate_tool_call_arguments(
                tool_name="query_clickhouse",
                arguments={},
                tools=tools,
            )
        self.assertIn("Missing required", missing_ctx.exception.message)

        with self.assertRaises(SkillRuntimeError) as extra_ctx:
            validate_tool_call_arguments(
                tool_name="query_clickhouse",
                arguments={"sql": "SELECT 1", "limit": 1},
                tools=tools,
            )
        self.assertIn("Unexpected argument", extra_ctx.exception.message)

        with self.assertRaises(SkillRuntimeError) as type_ctx:
            validate_tool_call_arguments(
                tool_name="query_clickhouse",
                arguments={"sql": 123},
                tools=tools,
            )
        self.assertIn("must be string", type_ctx.exception.message)

        with self.assertRaises(SkillRuntimeError) as enum_ctx:
            validate_tool_call_arguments(
                tool_name="load_skill",
                arguments={"skill_name": "unknown-skill"},
                tools=tools,
            )
        self.assertIn("must be one of", enum_ctx.exception.message)

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

    async def test_run_skill_script_executes_only_approved_manifest_entry(self) -> None:
        from app.database.repositories.skills import SkillRepository

        script_content = "print('latency ok')\n"
        skill = FakeSkill(
            name="check-kpis",
            skill_md="# check_kpis",
            bundled_files={
                "scripts/check_latency.py": {
                    "encoding": "utf-8",
                    "content": script_content,
                    "media_type": "text/x-python",
                    "size": len(script_content),
                }
            },
            script_manifest={
                "scripts/check_latency.py": {
                    "status": "passed",
                    "script_hash": "sha256:0229d2bd11fdab2d62f5d7f352095d490d56b108cd5bf343112158445ceca4c7",
                    "runtime": {"type": "python_script", "arguments_mode": "args_json"},
                    "limits": {"timeout_seconds": 15},
                }
            },
        )

        class FakeSandboxExecutor:
            received = {}

            async def execute_skill_script(self, **kwargs):
                FakeSandboxExecutor.received = kwargs
                return types.SimpleNamespace(
                    stdout="latency ok",
                    stderr="",
                    exit_code=0,
                    timed_out=False,
                )

        orig_get = SkillRepository.get_skill_by_name
        SkillRepository.get_skill_by_name = MagicMock(return_value=skill)
        try:
            with patch(
                "app.sandbox.docker_executor.build_sandbox_executor_from_settings",
                return_value=FakeSandboxExecutor(),
            ):
                output, truncated = await execute_builtin_tool(
                    tool_name="run_skill_script",
                    arguments={
                        "skill_name": "check-kpis",
                        "script_path": "scripts/check_latency.py",
                        "arguments": {"site_id": "site-a"},
                    },
                    db=object(),
                    settings=types.SimpleNamespace(SANDBOX_ENABLED=True),
                )

        finally:
            SkillRepository.get_skill_by_name = orig_get

        self.assertEqual("latency ok", output)
        self.assertFalse(truncated)
        self.assertEqual("scripts/check_latency.py", FakeSandboxExecutor.received["script_path"])
        self.assertEqual({"site_id": "site-a"}, FakeSandboxExecutor.received["arguments"])

    async def test_run_skill_script_rejects_hash_mismatch(self) -> None:
        from app.database.repositories.skills import SkillRepository

        skill = FakeSkill(
            name="check-kpis",
            skill_md="# check_kpis",
            bundled_files={
                "scripts/check_latency.py": {
                    "encoding": "utf-8",
                    "content": "print('changed')\n",
                    "media_type": "text/x-python",
                    "size": 17,
                }
            },
            script_manifest={
                "scripts/check_latency.py": {
                    "status": "passed",
                    "script_hash": "sha256:not-the-current-hash",
                    "runtime": {"type": "python_script", "arguments_mode": "args_json"},
                }
            },
        )
        orig_get = SkillRepository.get_skill_by_name
        SkillRepository.get_skill_by_name = MagicMock(return_value=skill)
        try:
            with self.assertRaises(SkillRuntimeError) as ctx:
                await execute_builtin_tool(
                    tool_name="run_skill_script",
                    arguments={
                        "skill_name": "check-kpis",
                        "script_path": "scripts/check_latency.py",
                        "arguments": {},
                    },
                    db=object(),
                )
        finally:
            SkillRepository.get_skill_by_name = orig_get

        self.assertIn("hash", ctx.exception.message.lower())

    async def test_run_skill_script_rejects_arguments_outside_approved_schema(self) -> None:
        from app.database.repositories.skills import SkillRepository

        script_content = "print('latency ok')\n"
        skill = FakeSkill(
            name="check-kpis",
            skill_md="# check_kpis",
            bundled_files={
                "scripts/check_latency.py": {
                    "encoding": "utf-8",
                    "content": script_content,
                    "media_type": "text/x-python",
                    "size": len(script_content),
                }
            },
            script_manifest={
                "scripts/check_latency.py": {
                    "status": "passed",
                    "script_hash": "sha256:0229d2bd11fdab2d62f5d7f352095d490d56b108cd5bf343112158445ceca4c7",
                    "input_schema": {
                        "type": "object",
                        "required": ["site_id", "window_minutes"],
                        "additionalProperties": False,
                        "properties": {
                            "site_id": {"type": "string"},
                            "window_minutes": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 1440,
                            },
                        },
                    },
                    "runtime": {"type": "python_script", "arguments_mode": "args_json"},
                }
            },
        )
        orig_get = SkillRepository.get_skill_by_name
        SkillRepository.get_skill_by_name = MagicMock(return_value=skill)
        try:
            with self.assertRaises(SkillRuntimeError) as ctx:
                await execute_builtin_tool(
                    tool_name="run_skill_script",
                    arguments={
                        "skill_name": "check-kpis",
                        "script_path": "scripts/check_latency.py",
                        "arguments": {"site_id": "site-a", "window_minutes": 0},
                    },
                    db=object(),
                )
        finally:
            SkillRepository.get_skill_by_name = orig_get

        self.assertIn("approved schema", ctx.exception.message)

    async def test_run_skill_script_rejects_output_outside_approved_contract(self) -> None:
        from app.database.repositories.skills import SkillRepository

        script_content = "print('json')\n"
        skill = FakeSkill(
            name="check-kpis",
            skill_md="# check_kpis",
            bundled_files={
                "scripts/check_latency.py": {
                    "encoding": "utf-8",
                    "content": script_content,
                    "media_type": "text/x-python",
                    "size": len(script_content),
                }
            },
            script_manifest={
                "scripts/check_latency.py": {
                    "status": "passed",
                    "script_hash": "sha256:cdd1faf5e95a8b93c654ac788f0d89e72177237755e5065927682cb6c5666b3a",
                    "input_schema": {"type": "object", "additionalProperties": True},
                    "output_contract": {
                        "mode": "json",
                        "schema": {
                            "type": "object",
                            "required": ["status"],
                            "additionalProperties": False,
                            "properties": {"status": {"type": "string"}},
                        },
                    },
                    "runtime": {"type": "python_script", "arguments_mode": "args_json"},
                }
            },
        )

        class FakeSandboxExecutor:
            async def execute_skill_script(self, **kwargs):
                return types.SimpleNamespace(
                    stdout='{"state":"ok"}',
                    stderr="",
                    exit_code=0,
                    timed_out=False,
                )

        orig_get = SkillRepository.get_skill_by_name
        SkillRepository.get_skill_by_name = MagicMock(return_value=skill)
        try:
            with patch(
                "app.sandbox.docker_executor.build_sandbox_executor_from_settings",
                return_value=FakeSandboxExecutor(),
            ):
                with self.assertRaises(SkillRuntimeError) as ctx:
                    await execute_builtin_tool(
                        tool_name="run_skill_script",
                        arguments={
                            "skill_name": "check-kpis",
                            "script_path": "scripts/check_latency.py",
                            "arguments": {},
                        },
                        db=object(),
                        settings=types.SimpleNamespace(SANDBOX_ENABLED=True),
                    )
        finally:
            SkillRepository.get_skill_by_name = orig_get

        self.assertIn("output contract", ctx.exception.message)

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

            async def execute_command(self, command: str, *, approval_confirmations=0):
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
                approval_confirmations=1,
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

            async def execute_command(self, command: str, *, approval_confirmations=0):
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
                approval_confirmations=1,
            )

        self.assertEqual("ok", output)
        self.assertFalse(truncated)
        self.assertEqual("node-b.internal", FakeSSHConnector.instances[0].kwargs["host"])

    async def test_ssh_resolver_accepts_legacy_json_node_host_map(self) -> None:
        class FakeSSHConnector:
            instances = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                FakeSSHConnector.instances.append(self)

            async def execute_command(self, command: str, *, approval_confirmations=0):
                return "ok", ""

            def close(self):
                pass

        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_NODE_HOST_MAP='{"site-a": "10.0.0.11"}',
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
                arguments={"node_name": "site-a", "command": "hostname"},
                db=object(),
                settings=settings,
                approval_confirmations=1,
            )

        self.assertEqual("ok", output)
        self.assertFalse(truncated)
        self.assertEqual("10.0.0.11", FakeSSHConnector.instances[0].kwargs["host"])

    async def test_ssh_tool_output_is_redacted_before_returning_to_llm(self) -> None:
        class FakeSSHConnector:
            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command: str, *, approval_confirmations=0):
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
                approval_confirmations=1,
            )

        self.assertFalse(truncated)
        self.assertNotIn("super-secret", output)
        self.assertNotIn("abc123", output)
        self.assertIn("[REDACTED]", output)

    async def test_ssh_known_hosts_error_explains_trust_configuration(self) -> None:
        from app.connectors.ssh import TelcoSSHConnector

        connector = TelcoSSHConnector(
            host="host.test",
            username="noc",
            password="pwd",
            port=2222,
        )

        async def raise_known_hosts_error(*args, **kwargs):
            raise RuntimeError("Server '[host.test]:2222' not found in known_hosts")

        with patch("app.connectors.ssh.asyncio.to_thread", raise_known_hosts_error):
            with self.assertRaises(ConnectorExecutionError) as ctx:
                await connector.execute_command("hostname")

        self.assertIn("known_hosts", ctx.exception.message)
        self.assertIn("SSH_KNOWN_HOSTS", ctx.exception.message)
        self.assertNotIn("SSH_AUTO_ADD_HOST_KEYS=true", ctx.exception.message)
        self.assertEqual("host.test", ctx.exception.details["host"])
        self.assertEqual(2222, ctx.exception.details["port"])

    async def test_ssh_strips_safe_output_limit_pipe_before_execution(self) -> None:
        class FakeSSHConnector:
            command = None

            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command: str, *, approval_confirmations=0):
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
                approval_confirmations=1,
            )

        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)
        self.assertEqual("ps -eo pid,ppid,comm,%mem,%cpu --sort=-%cpu", FakeSSHConnector.command)
        self.assertEqual("ok", output)
        self.assertFalse(truncated)

    def test_classify_builtin_risk_checks_dangerous_terms(self) -> None:
        # Safe command
        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "show configuration"},
        )
        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

        # Dangerous command
        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "systemctl restart telco_service"},
        )
        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    def test_ssh_state_changes_require_approval(self) -> None:
        for command in ("touch /tmp/pwn", "sed -i s/a/b/ /etc/app.conf", "mkdir /tmp/work"):
            with self.subTest(command=command):
                risk = classify_builtin_risk(
                    tool_name="run_ssh_command",
                    arguments={"node_name": "site-a", "command": command},
                )
                self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    def test_ssh_critical_commands_require_two_confirmations(self) -> None:
        risk = classify_builtin_risk(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "rm -rf /"},
        )
        confirmations = required_approval_confirmations(
            tool_name="run_ssh_command",
            arguments={"node_name": "site-a", "command": "rm -rf /"},
        )

        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)
        self.assertEqual(2, confirmations)

    def test_ssh_sensitive_file_reads_remain_blocked_by_validation(self) -> None:
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
                with self.assertRaises(SafetyViolationError):
                    classify_builtin_risk(
                        tool_name="run_ssh_command",
                        arguments={"node_name": "site-a", "command": command},
                    )

    async def test_critical_ssh_execution_requires_both_confirmations(self) -> None:
        settings = types.SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_NODE_HOST_MAP="site-a=10.0.0.10",
            SSH_HOST="",
            SSH_PORT=22,
            SSH_USER="noc",
            SSH_PASSWORD="pwd",
            SSH_TIMEOUT_SECONDS=5,
            SSH_KNOWN_HOSTS="",
            SSH_AUTO_ADD_HOST_KEYS=False,
        )

        with self.assertRaises(SafetyViolationError):
            await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-a", "command": "rm -rf /var/tmp/cache"},
                db=object(),
                settings=settings,
                approval_confirmations=1,
            )

        class FakeSSHConnector:
            confirmations = None

            def __init__(self, **kwargs):
                pass

            async def execute_command(self, command, *, approval_confirmations=0):
                FakeSSHConnector.confirmations = approval_confirmations
                return "removed", ""

            def close(self):
                return None

        with patch("app.agent.builtin_tools.TelcoSSHConnector", FakeSSHConnector):
            output, _ = await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-a", "command": "rm -rf /var/tmp/cache"},
                db=object(),
                settings=settings,
                approval_confirmations=2,
            )

        self.assertEqual(2, FakeSSHConnector.confirmations)
        self.assertEqual("removed", output)

        with self.assertRaises(SafetyViolationError):
            await execute_builtin_tool(
                tool_name="run_ssh_command",
                arguments={"node_name": "site-a", "command": "cat ~/.ssh/id_rsa"},
                db=object(),
                settings=settings,
                approval_confirmations=2,
            )

    def test_clickhouse_mutations_require_approval(self) -> None:
        for sql in (
            "DROP TABLE alarms",
            "ALTER TABLE alarms DELETE WHERE id = 1",
        ):
            with self.subTest(sql=sql):
                risk = classify_builtin_risk(
                    tool_name="query_clickhouse",
                    arguments={"sql": sql},
                )
                self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    def test_multiple_sql_statements_are_rejected_by_validation(self) -> None:
        with self.assertRaises(SafetyViolationError):
            classify_builtin_risk(
                tool_name="query_clickhouse",
                arguments={"sql": "SELECT 1; DROP TABLE alarms"},
            )

    def test_clickhouse_select_requires_approval_even_when_read_only(self) -> None:
        risk = classify_builtin_risk(
            tool_name="query_clickhouse",
            arguments={
                "sql": "WITH 5 AS threshold SELECT * FROM alarms WHERE severity > threshold"
            },
        )
        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    def test_clickhouse_system_database_select_is_read_only(self) -> None:
        risk = classify_builtin_risk(
            tool_name="query_clickhouse",
            arguments={"sql": "SELECT count() FROM system.tables"},
        )
        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    def test_postgres_mutations_require_approval(self) -> None:
        for sql in (
            "DROP TABLE alarms",
            "UPDATE inventory SET status = 'down'",
        ):
            with self.subTest(sql=sql):
                risk = classify_builtin_risk(
                    tool_name="query_postgres",
                    arguments={"sql": sql},
                )
                self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    def test_postgres_select_requires_approval_even_when_read_only(self) -> None:
        risk = classify_builtin_risk(
            tool_name="query_postgres",
            arguments={"sql": "SELECT * FROM inventory LIMIT 1"},
        )
        self.assertEqual(ExecutionMode.REQUIRE_APPROVAL.value, risk)

    async def test_postgres_mutation_only_executes_after_approval(self) -> None:
        settings = types.SimpleNamespace(
            EXTERNAL_POSTGRES_HOST="postgres.example.test",
            EXTERNAL_POSTGRES_PORT=5432,
            EXTERNAL_POSTGRES_USER="operator",
            EXTERNAL_POSTGRES_PASSWORD="secret",
            EXTERNAL_POSTGRES_DATABASE="telecom",
            EXTERNAL_CONNECTOR_TIMEOUT_SECONDS=5,
            QUERY_MAX_RESULT_ROWS=20,
        )

        with self.assertRaises(SafetyViolationError):
            await execute_builtin_tool(
                tool_name="query_postgres",
                arguments={"sql": "UPDATE inventory SET status = 'down' WHERE id = 7"},
                db=object(),
                settings=settings,
            )

        class FakePostgresConnector:
            read_only = None

            def __init__(self, **kwargs):
                FakePostgresConnector.read_only = kwargs["read_only"]

            async def query(self, sql, params=None):
                return [{"status": "SUCCESS", "affected_rows": 1}]

            def close(self):
                return None

        with patch("app.agent.builtin_tools.TelcoPostgresConnector", FakePostgresConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="query_postgres",
                arguments={"sql": "UPDATE inventory SET status = 'down' WHERE id = 7"},
                db=object(),
                settings=settings,
                approval_confirmations=1,
            )

        self.assertFalse(FakePostgresConnector.read_only)
        self.assertIn('"affected_rows": 1', output)
        self.assertFalse(truncated)

    async def test_clickhouse_mutation_only_executes_after_approval(self) -> None:
        settings = types.SimpleNamespace(
            CLICKHOUSE_HOST="clickhouse.example.test",
            CLICKHOUSE_PORT=8123,
            CLICKHOUSE_USER="operator",
            CLICKHOUSE_PASSWORD="secret",
            CLICKHOUSE_DATABASE="telecom",
            EXTERNAL_CONNECTOR_TIMEOUT_SECONDS=5,
            QUERY_MAX_RESULT_ROWS=20,
        )

        with self.assertRaises(SafetyViolationError):
            await execute_builtin_tool(
                tool_name="query_clickhouse",
                arguments={"sql": "ALTER TABLE alarms DELETE WHERE id = 7"},
                db=object(),
                settings=settings,
            )

        class FakeClickHouseConnector:
            allow_mutation = None

            def __init__(self, **kwargs):
                pass

            async def execute(self, sql, *, allow_mutation=False):
                FakeClickHouseConnector.allow_mutation = allow_mutation
                return [{"status": "SUCCESS"}]

            def close(self):
                return None

        with patch("app.agent.builtin_tools.TelcoClickHouseConnector", FakeClickHouseConnector):
            output, truncated = await execute_builtin_tool(
                tool_name="query_clickhouse",
                arguments={"sql": "ALTER TABLE alarms DELETE WHERE id = 7"},
                db=object(),
                settings=settings,
                approval_confirmations=1,
            )

        self.assertTrue(FakeClickHouseConnector.allow_mutation)
        self.assertIn('"status": "SUCCESS"', output)
        self.assertFalse(truncated)


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

    def test_mutation_uses_command_with_readonly_disabled(self) -> None:
        from app.connectors.clickhouse import TelcoClickHouseConnector

        client = MagicMock()
        client.command.return_value = "OK"
        connector = TelcoClickHouseConnector(host="clickhouse.example.test")
        connector._client = client

        result = connector._sync_execute(
            "ALTER TABLE alarms DELETE WHERE id = 7",
            allow_mutation=True,
        )

        self.assertEqual([{"status": "SUCCESS", "result": "OK"}], result)
        client.command.assert_called_once_with(
            "ALTER TABLE alarms DELETE WHERE id = 7",
            parameters=None,
            settings={"readonly": 0},
        )


class DockerSandboxExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_skill_script_writes_args_json_and_builds_docker_command(self) -> None:
        from app.sandbox.docker_executor import SANDBOX_WORKSPACE_DIR, DockerSandboxExecutor

        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            workspace = command[command.index("-v") + 1].split(":")[0]
            with open(f"{workspace}/args.json", encoding="utf-8") as handle:
                captured["args_json"] = handle.read()
            return types.SimpleNamespace(stdout="latency ok", stderr="", returncode=0)

        executor = DockerSandboxExecutor(image="python:3.12-slim")
        with (
            patch("app.sandbox.docker_executor.shutil.which", return_value="/usr/bin/docker"),
            patch("app.sandbox.docker_executor.subprocess.run", side_effect=fake_run),
        ):
            result = await executor.execute_skill_script(
                script_path="scripts/check.py",
                arguments={},
                bundled_files={
                    "scripts/check.py": {"encoding": "utf-8", "content": "print('latency ok')\n"}
                },
            )

        self.assertEqual("latency ok", result.stdout)
        self.assertEqual(0, result.exit_code)
        self.assertFalse(result.timed_out)
        self.assertEqual("{}", captured["args_json"])
        command = captured["command"]
        self.assertIn("--network", command)
        self.assertIn("none", command)
        self.assertIn(SANDBOX_WORKSPACE_DIR, command)
        self.assertEqual(
            ["python:3.12-slim", "python3", "scripts/check.py"],
            command[-3:],
        )

    async def test_skill_script_passes_spaced_path_as_single_argv(self) -> None:
        from app.sandbox.docker_executor import DockerSandboxExecutor

        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return types.SimpleNamespace(stdout="ok", stderr="", returncode=0)

        executor = DockerSandboxExecutor()
        with (
            patch("app.sandbox.docker_executor.shutil.which", return_value="/usr/bin/docker"),
            patch("app.sandbox.docker_executor.subprocess.run", side_effect=fake_run),
        ):
            await executor.execute_skill_script(script_path="scripts/check latency.py")

        # Không dùng shell → đường dẫn có khoảng trắng vẫn là MỘT phần tử argv, không cần quote.
        self.assertEqual("scripts/check latency.py", captured["command"][-1])

    async def test_skill_script_reports_timeout(self) -> None:
        import subprocess

        from app.sandbox.docker_executor import DockerSandboxExecutor

        def fake_run(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=1)

        executor = DockerSandboxExecutor(timeout_seconds=1)
        with (
            patch("app.sandbox.docker_executor.shutil.which", return_value="/usr/bin/docker"),
            patch("app.sandbox.docker_executor.subprocess.run", side_effect=fake_run),
        ):
            result = await executor.execute_skill_script(script_path="scripts/slow.py")

        self.assertTrue(result.timed_out)
        self.assertEqual(124, result.exit_code)

    async def test_skill_script_requires_docker_on_host(self) -> None:
        from app.sandbox.docker_executor import DockerSandboxExecutor

        executor = DockerSandboxExecutor()
        with patch("app.sandbox.docker_executor.shutil.which", return_value=None):
            with self.assertRaises(SkillRuntimeError) as ctx:
                await executor.execute_skill_script(script_path="scripts/check.py")

        self.assertIn("Docker", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
