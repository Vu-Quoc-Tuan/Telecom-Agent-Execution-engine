from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import inspect

from app.common.enums import StepType
from app.config import Settings
from app.database.models.approval_requests import ApprovalRequest
from app.database.models.audit_logs import AuditLog
from app.database.models.chat_messages import ChatMessage
from app.database.models.run_steps import RunStep
from app.database.models.skills import Skill


class SettingsContractTests(unittest.TestCase):
    def test_settings_can_load_without_external_connector_credentials(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual("", settings.CLICKHOUSE_HOST)
        self.assertEqual("", settings.EXTERNAL_POSTGRES_HOST)
        self.assertEqual("", settings.SSH_HOST)

    def test_build_llm_gateway_normalizes_default_provider_case(self) -> None:
        from app.config import build_llm_gateway

        settings = Settings(
            _env_file=None,
            PROVIDER="OpenAI",
            OPENAI_API_KEY="sk-test",
            ANTHROPIC_API_KEY="",
        )

        gateway = build_llm_gateway(settings)

        from app.llm.langchain_adapter import LangChainChatAdapter

        adapter = gateway.get_adapter()
        self.assertEqual("openai", adapter.provider)
        self.assertIsInstance(adapter, LangChainChatAdapter)


class ModelContractTests(unittest.TestCase):
    def test_approval_request_is_single_confirmation(self) -> None:
        columns = {column.key for column in inspect(ApprovalRequest).columns}

        self.assertNotIn("required_confirmations", columns)
        self.assertNotIn("confirmation_count", columns)

    def test_skill_persists_connector_and_risk_metadata(self) -> None:
        columns = {column.key for column in inspect(Skill).columns}

        self.assertIn("skill_md", columns)
        self.assertIn("frontmatter", columns)
        self.assertIn("bundled_files", columns)

    def test_chat_message_supports_tool_role(self) -> None:
        constraints = " ".join(
            str(constraint.sqltext)
            for constraint in ChatMessage.__table__.constraints
            if hasattr(constraint, "sqltext")
        )

        self.assertNotIn("'system'", constraints)
        self.assertIn("'tool'", constraints)

    def test_audit_log_is_append_only_without_hard_foreign_keys(self) -> None:
        foreign_keys = list(AuditLog.__table__.foreign_keys)

        self.assertEqual([], foreign_keys)

    def test_run_step_type_contract_matches_active_timeline_steps(self) -> None:
        self.assertEqual(
            {
                "llm_call",
                "tool_call",
                "approval",
                "error",
            },
            {step_type.value for step_type in StepType},
        )

        constraints = " ".join(
            str(constraint.sqltext)
            for constraint in RunStep.__table__.constraints
            if hasattr(constraint, "sqltext")
        )

        self.assertIn("'llm_call'", constraints)
        self.assertIn("'tool_call'", constraints)
        self.assertIn("'approval'", constraints)
        self.assertIn("'error'", constraints)
        self.assertNotIn("'request_received'", constraints)
        self.assertNotIn("'analysis_summary'", constraints)
        self.assertNotIn("'final_answer'", constraints)


class RepositoryImportTests(unittest.TestCase):
    def test_required_repositories_exist(self) -> None:
        from app.database.repositories.approvals import ApprovalRepository
        from app.database.repositories.audit_logs import AuditLogRepository
        from app.database.repositories.run_steps import RunStepRepository
        from app.database.repositories.sessions import SessionRepository
        from app.database.repositories.tool_calls import ToolCallRepository

        self.assertTrue(ApprovalRepository)
        self.assertTrue(AuditLogRepository)
        self.assertTrue(RunStepRepository)
        self.assertTrue(SessionRepository)
        self.assertTrue(ToolCallRepository)

    def test_skill_repository_can_delete_test_skill_records(self) -> None:
        from app.database.repositories.skills import SkillRepository

        skill_id = uuid4()
        skill = SimpleNamespace(id=skill_id)

        class FakeDb:
            def __init__(self):
                self.deleted = None
                self.committed = False

            def get(self, model, record_id):
                return skill if record_id == skill_id else None

            def delete(self, record):
                self.deleted = record

            def commit(self):
                self.committed = True

        db = FakeDb()

        self.assertTrue(SkillRepository.delete_skill(db, skill_id))
        self.assertIs(skill, db.deleted)
        self.assertTrue(db.committed)
        self.assertFalse(SkillRepository.delete_skill(FakeDb(), uuid4()))

    def test_tool_call_repository_aggregates_skill_package_telemetry(self) -> None:
        from app.database.repositories.tool_calls import ToolCallRepository

        created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

        class FakeDb:
            statement = None

            def execute(self, statement):
                self.statement = statement
                return self

            def all(self):
                return [
                    SimpleNamespace(
                        matched_skill_name="no-alarm-enrichment",
                        call_count=2,
                        average_latency_ms=200,
                        error_count=1,
                        last_called_at=created_at,
                    )
                ]

        telemetry = ToolCallRepository.get_skill_telemetry(
            fake_db := FakeDb(),
            ["no-alarm-enrichment"],
        )

        self.assertEqual(2, telemetry["no-alarm-enrichment"]["call_count"])
        self.assertEqual(200, telemetry["no-alarm-enrichment"]["average_latency_ms"])
        self.assertEqual(0.5, telemetry["no-alarm-enrichment"]["error_rate"])
        self.assertEqual(1, telemetry["no-alarm-enrichment"]["error_count"])
        statement_sql = str(fake_db.statement).upper()
        self.assertIn("GROUP BY", statement_sql)
        self.assertIn("CREATED_AT", statement_sql)

    def test_complete_step_merges_existing_metadata(self) -> None:
        from app.database.repositories.run_steps import RunStepRepository

        step = SimpleNamespace(
            id=uuid4(),
            status="running",
            summary=None,
            metadata_json={"tool_name": "get_active_alarms", "started_by": "agent"},
            completed_at=None,
        )

        class FakeDb:
            def get(self, model, step_id):
                return step

            def commit(self):
                pass

            def refresh(self, record):
                pass

        completed = RunStepRepository.complete_step(
            FakeDb(),
            step.id,
            status="completed",
            summary="ok",
            metadata={"usage": {"tokens": 10}, "started_by": "runtime"},
        )

        self.assertIs(completed, step)
        self.assertEqual(
            {
                "tool_name": "get_active_alarms",
                "started_by": "runtime",
                "usage": {"tokens": 10},
            },
            step.metadata_json,
        )


if __name__ == "__main__":
    unittest.main()
