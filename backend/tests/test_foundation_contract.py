from __future__ import annotations

import unittest

from sqlalchemy import inspect

from app.common.enums import StepType
from app.config import Settings
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


class ModelContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
