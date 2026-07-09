from __future__ import annotations

import asyncio
import io
import unittest
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError


class FakeUploadFile:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.content = content

    async def read(self, size: int = -1) -> bytes:
        return self.content if size < 0 else self.content[:size]


class ApplicationImportTests(unittest.TestCase):
    def test_application_import_does_not_open_external_connections(self) -> None:
        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/api/v1/chat/stream", paths)
        self.assertIn("/api/v1/chat/options", paths)
        self.assertIn("/api/v1/sessions", paths)
        self.assertIn("/api/v1/sessions/{session_id}/messages", paths)
        self.assertIn("/api/v1/runs/{run_id}/timeline", paths)
        self.assertIn("/api/v1/runs/{run_id}/cancel", paths)
        self.assertIn("/api/v1/runs/{run_id}/interventions", paths)
        self.assertIn("/api/v1/runs/mark-timeouts", paths)
        self.assertIn("/api/v1/skills/inspect", paths)
        chat_route = next(route for route in app.routes if route.path == "/api/v1/chat/stream")
        self.assertEqual({"POST"}, chat_route.methods)
        options_route = next(route for route in app.routes if route.path == "/api/v1/chat/options")
        self.assertEqual({"GET"}, options_route.methods)
        history_route = next(
            route for route in app.routes if route.path == "/api/v1/sessions/{session_id}/messages"
        )
        self.assertEqual({"GET"}, history_route.methods)
        inspect_route = next(
            route for route in app.routes if route.path == "/api/v1/skills/inspect"
        )
        self.assertEqual({"POST"}, inspect_route.methods)

    def test_chat_options_expose_openai_and_claude_availability(self) -> None:
        from app.api.chat import get_chat_options

        ready_skill = SimpleNamespace(name="check-kpis", description="Kiểm tra KPI theo ca.")

        class FakeAdapter:
            model = "gpt-primary"

        class FakeGateway:
            providers = ("openai",)

            @staticmethod
            def get_adapter(provider):
                self = FakeAdapter()
                self.provider = provider
                return self

        with (
            patch("app.api.chat.get_llm_gateway", return_value=FakeGateway()),
            patch("app.api.chat.settings.PROVIDER", "openai"),
            patch("app.api.chat.settings.OPENAI_MODEL_NAME", "gpt-primary"),
            patch("app.api.chat.settings.ANTHROPIC_MODEL_NAME", "claude-sonnet"),
            patch(
                "app.api.chat.SkillRepository.list_ready_skills",
                return_value=[ready_skill],
            ),
            patch(
                "app.api.chat.list_backend_owned_capabilities",
                return_value=[
                    {
                        "name": "get_site_alarm_summary",
                        "connector": "clickhouse",
                        "description": "Alarm summary",
                    }
                ],
            ),
        ):
            payload = get_chat_options(db=object())

        self.assertEqual(["OpenAI", "Claude"], [item["label"] for item in payload["models"]])
        self.assertEqual([True, False], [item["available"] for item in payload["models"]])
        self.assertEqual(
            {"provider": "openai", "model": "gpt-primary"},
            payload["default_model"],
        )
        self.assertEqual("check-kpis", payload["skills"][0]["name"])
        self.assertEqual("get_site_alarm_summary", payload["capabilities"][0]["name"])

    def test_specific_skill_mode_requires_a_skill_name(self) -> None:
        from app.api.chat import ChatStreamBody

        with self.assertRaises(ValidationError):
            ChatStreamBody(
                session_id=uuid4(),
                user_message="Kiểm tra KPI",
                skill_mode="specific",
            )

    def test_chat_stream_rejects_an_unsupported_provider(self) -> None:
        from app.api.chat import ChatStreamBody, stream_agent_conversation

        class FakeGateway:
            providers = ("openai",)

        body = ChatStreamBody(
            session_id=uuid4(),
            user_message="Kiểm tra KPI",
            provider="not-a-provider",
        )

        with (
            patch("app.api.chat.get_llm_gateway", return_value=FakeGateway()),
            self.assertRaises(HTTPException) as raised,
        ):
            asyncio.run(stream_agent_conversation(body))

        self.assertEqual(422, raised.exception.status_code)
        self.assertIn("not-a-provider", str(raised.exception.detail))

    def test_checkpoint_serializer_allows_llm_schema_types(self) -> None:
        from app.agent.checkpointer import build_checkpoint_serializer
        from app.agent.tool_batch_planner import ToolBatchPlan, ToolPlanItem
        from app.llm.schemas import LLMMessage, LLMToolDefinition, MessageRole, NormalizedToolCall

        serializer = build_checkpoint_serializer()
        payload = LLMMessage(role=MessageRole.USER, content="check node")

        restored = serializer.loads_typed(serializer.dumps_typed(payload))

        self.assertEqual(payload, restored)

        # New-style plan (without deprecated tool_catalog / ready_skill_names).
        plan = ToolBatchPlan(
            route="execute_tools",
            items=[
                ToolPlanItem(
                    index=0,
                    tool_call=NormalizedToolCall(id="call-1", name="ping_node", arguments={}),
                    risk_level="auto_execute",
                )
            ],
        )
        restored_plan = serializer.loads_typed(serializer.dumps_typed(plan))
        self.assertEqual(plan, restored_plan)
        self.assertIsInstance(restored_plan, ToolBatchPlan)

        # Backward-compat: old checkpoints that include tool_catalog / ready_skill_names
        # must still deserialize without error.
        legacy_plan = ToolBatchPlan(
            route="execute_tools",
            items=[
                ToolPlanItem(
                    index=0,
                    tool_call=NormalizedToolCall(id="call-1", name="ping_node", arguments={}),
                    risk_level="auto_execute",
                )
            ],
            tool_catalog=[
                LLMToolDefinition(
                    name="ping_node", description="ping a node", input_schema={"type": "object"}
                )
            ],
            ready_skill_names=["ping_node"],
        )
        restored_legacy = serializer.loads_typed(serializer.dumps_typed(legacy_plan))
        self.assertEqual(legacy_plan, restored_legacy)
        self.assertIsInstance(restored_legacy, ToolBatchPlan)

    def test_timeout_sweeper_uses_lifecycle_service(self) -> None:
        from app import main

        fake_db = object()

        class FakeSessionContext:
            def __enter__(self):
                return fake_db

            def __exit__(self, exc_type, exc, tb):
                return False

        with (
            patch("app.main.SessionLocal", return_value=FakeSessionContext()),
            patch(
                "app.main.RunLifecycleService.mark_timed_out_runs",
                return_value=[object(), object()],
            ) as mark_timeouts,
            patch.object(main.settings, "RUN_TIMEOUT_SECONDS", 123),
            patch.object(main.settings, "RUN_TIMEOUT_SWEEPER_LIMIT", 7),
        ):
            count = main.sweep_timed_out_runs_once()

        self.assertEqual(2, count)
        mark_timeouts.assert_called_once_with(db=fake_db, timeout_seconds=123, limit=7)

    def test_database_startup_probe_executes_select_one(self) -> None:
        from app import main

        executed = []

        class FakeSessionContext:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, statement):
                executed.append(str(statement))

        with patch("app.main.SessionLocal", return_value=FakeSessionContext()):
            main.verify_database_connectivity()

        self.assertTrue(any("SELECT 1" in statement for statement in executed))

    def test_runtime_resources_accept_legacy_json_ssh_node_host_map(self) -> None:
        from app.api.resources import list_runtime_resources

        fake_settings = SimpleNamespace(
            SSH_ALLOWED_NODES="site-a",
            SSH_NODE_HOST_MAP='{"site-a": "10.0.0.11"}',
            SSH_HOST="",
            SSH_PORT=22,
            SSH_USER="noc",
            CLICKHOUSE_HOST="",
            CLICKHOUSE_USER="",
            CLICKHOUSE_PORT=8123,
            CLICKHOUSE_DATABASE="alarm_data",
            EXTERNAL_POSTGRES_HOST="",
            EXTERNAL_POSTGRES_USER="",
            EXTERNAL_POSTGRES_PORT=5432,
            EXTERNAL_POSTGRES_DATABASE="postgres",
        )

        with patch("app.api.resources.settings", fake_settings):
            resources = list_runtime_resources()

        ssh_resource = next(resource for resource in resources if resource["id"] == "ssh-site-a")
        self.assertTrue(ssh_resource["status"] == "connected")
        self.assertEqual("10.0.0.11", ssh_resource["region"])

    def test_session_messages_endpoint_serializes_chat_history(self) -> None:
        from app.api.sessions import get_chat_session_messages

        session_id = uuid4()
        message_id = uuid4()
        run_id = uuid4()
        created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        fake_message = SimpleNamespace(
            id=message_id,
            session_id=session_id,
            run_id=run_id,
            role="user",
            content="kiểm tra lag trên hanoi-core-01",
            status="completed",
            sequence_no=1,
            metadata_json={"source": "chat"},
            created_at=created_at,
        )

        with (
            patch("app.api.sessions.SessionService.get_active_session", return_value=object()),
            patch(
                "app.api.sessions.MessageRepository.get_chat_history",
                return_value=[fake_message],
            ),
        ):
            payload = get_chat_session_messages(session_id=session_id, db=object())

        self.assertEqual(
            payload,
            [
                {
                    "id": str(message_id),
                    "session_id": str(session_id),
                    "run_id": str(run_id),
                    "role": "user",
                    "content": "kiểm tra lag trên hanoi-core-01",
                    "status": "completed",
                    "sequence_no": 1,
                    "metadata": {"source": "chat"},
                    "created_at": created_at.isoformat(),
                }
            ],
        )


class SkillUploadApiTests(unittest.IsolatedAsyncioTestCase):
    def test_list_skills_includes_runtime_telemetry(self) -> None:
        from app.api.skills import list_skills

        skill_id = uuid4()
        created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        fake_skill = SimpleNamespace(
            id=skill_id,
            name="no-alarm-enrichment",
            description="No alarm enrichment workflow.",
            skill_md="# No alarm enrichment\n\nRun the approved checks.",
            version="1.0.0",
            status="ready",
            is_malicious=False,
            security_review_log=None,
            created_at=created_at,
            updated_at=created_at,
            frontmatter={},
            bundled_files={},
        )
        telemetry = {
            "no-alarm-enrichment": {
                "call_count": 3,
                "average_latency_ms": 125,
                "error_rate": 1 / 3,
                "error_count": 1,
                "last_called_at": created_at.isoformat(),
            }
        }

        with (
            patch("app.api.skills.SkillRepository.list_skills", return_value=[fake_skill]),
            patch("app.api.skills.ToolCallRepository.get_skill_telemetry", return_value=telemetry),
        ):
            payload = list_skills(db=object())

        self.assertEqual(telemetry["no-alarm-enrichment"], payload[0]["telemetry"])
        self.assertEqual(fake_skill.skill_md, payload[0]["skill_md"])

    async def test_upload_rejects_non_zip_filename(self) -> None:
        from app.api.skills import upload_and_verify_skill_pipeline

        file = FakeUploadFile(filename="SKILL.md", content=b"content")
        with self.assertRaises(HTTPException) as ctx:
            await upload_and_verify_skill_pipeline(file=file, db=object())

        self.assertEqual(415, ctx.exception.status_code)

    async def test_upload_preserves_service_conflict_status(self) -> None:
        from app.api.skills import upload_and_verify_skill_pipeline
        from app.services.skills import SkillValidationError

        error = SkillValidationError(
            status="CONFLICT",
            message="Skill already exists.",
            logs=["duplicate"],
            http_status_code=409,
        )
        file = FakeUploadFile(filename="skill.zip", content=b"PK")
        with (
            patch("app.api.skills.get_llm_gateway", return_value=object()),
            patch(
                "app.api.skills.SkillValidationService.upload_skill",
                new=AsyncMock(side_effect=error),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await upload_and_verify_skill_pipeline(file=file, db=object())

        self.assertEqual(409, ctx.exception.status_code)
        self.assertEqual("CONFLICT", ctx.exception.detail["status"])

    def test_approve_rejects_skill_with_unapproved_script_manifest(self) -> None:
        from app.api.skills import approve_skill_for_agent

        skill_id = uuid4()
        fake_skill = SimpleNamespace(
            id=skill_id,
            status="testing",
            script_manifest={
                "scripts/check.py": {
                    "status": "pending_sandbox",
                    "script_hash": "sha256:abc",
                }
            },
        )

        with (
            patch("app.api.skills.SkillRepository.get_skill_by_id", return_value=fake_skill),
            patch("app.api.skills.SkillRepository.approve_skill") as approve_skill,
        ):
            with self.assertRaises(HTTPException) as ctx:
                approve_skill_for_agent(skill_id=skill_id, db=object())

        self.assertEqual(409, ctx.exception.status_code)
        self.assertIn("scripts/check.py", ctx.exception.detail["scripts"])
        approve_skill.assert_not_called()

    async def test_inspect_skill_package_reads_metadata_from_zip(self) -> None:
        from app.api.skills import inspect_skill_package

        skill_md = """---
name: check-olt-signal
description: Check OLT optical signal alarms for telecom NOC troubleshooting.
metadata:
  version: "1.2.3"
---
# Check OLT Signal
Read status only.
"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("check-olt-signal/SKILL.md", skill_md)
            archive.writestr("check-olt-signal/scripts/check.py", "def run():\n    return 'ok'\n")

        file = FakeUploadFile(filename="check-olt-signal.zip", content=buffer.getvalue())

        payload = await inspect_skill_package(file=file)

        self.assertEqual("check-olt-signal", payload["name"])
        self.assertEqual("1.2.3", payload["frontmatter"].get("metadata", {}).get("version"))
        self.assertIn("OLT optical signal", payload["description"])
        self.assertEqual(
            [
                {
                    "path": "scripts/check.py",
                    "encoding": "utf-8",
                    "media_type": "text/x-python",
                    "size": 27,
                }
            ],
            payload["files"],
        )


if __name__ == "__main__":
    unittest.main()
