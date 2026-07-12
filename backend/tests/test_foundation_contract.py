from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import inspect

from app.common.enums import StepType
from app.config import Settings
from app.database.models.approval_requests import ApprovalRequest
from app.database.models.chat_messages import ChatMessage
from app.database.models.run_steps import RunStep
from app.database.models.skills import Skill


class ConfigParsingTests(unittest.TestCase):
    def test_parse_positive_int_preserves_existing_fallback_behavior(self) -> None:
        from app.common.config_parsing import parse_positive_int

        self.assertEqual(12, parse_positive_int({"limit": "12"}, "limit", 5))
        self.assertEqual(5, parse_positive_int({"limit": "invalid"}, "limit", 5))
        self.assertEqual(5, parse_positive_int({"limit": 0}, "limit", 5))
        self.assertIsNone(parse_positive_int({}, "limit"))


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

    def test_complete_step_uses_atomic_jsonb_metadata_merge(self) -> None:
        from app.database.repositories.run_steps import RunStepRepository

        step = SimpleNamespace(
            id=uuid4(),
            status="running",
            summary=None,
            metadata_json={"tool_name": "get_active_alarms", "started_by": "agent"},
            completed_at=None,
        )

        class FakeDb:
            statement = None

            def scalar(self, statement):
                self.statement = statement
                return step

            def get(self, model, step_id):
                return step

            def commit(self):
                pass

            def refresh(self, record):
                pass

        db = FakeDb()
        completed = RunStepRepository.complete_step(
            db,
            step.id,
            status="completed",
            summary="ok",
            metadata={"usage": {"tokens": 10}, "started_by": "runtime"},
        )

        self.assertIs(completed, step)
        statement_sql = str(db.statement)
        self.assertIn("metadata_json ||", statement_sql)


if __name__ == "__main__":
    unittest.main()
