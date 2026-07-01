from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


class ApprovalPendingListingTests(unittest.TestCase):
    def test_pending_request_lookup_is_read_only(self) -> None:
        from app.database.repositories.approvals import ApprovalRepository

        class FakeScalarResult:
            def all(self):
                return []

        class FakeDb:
            committed = False

            def scalars(self, statement):
                self.statement = statement
                return FakeScalarResult()

            def execute(self, statement):
                raise AssertionError("pending lookup must not issue write statements")

            def commit(self):
                self.committed = True

        db = FakeDb()

        self.assertEqual([], ApprovalRepository.get_pending_requests(db))
        self.assertFalse(db.committed)

    def test_pending_approval_details_are_loaded_with_one_joined_query(self) -> None:
        from app.database.repositories.approvals import ApprovalRepository

        requested_at = datetime.now(UTC)
        approval = SimpleNamespace(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            status="pending",
            requested_at=requested_at,
            expires_at=requested_at + timedelta(minutes=30),
            resolved_at=None,
        )
        tool_call = SimpleNamespace(
            skill_name="restart_service",
            arguments_json={"node_name": "node-1", "service": "mme"},
            connector_name="ssh",
            risk_level="require_approval",
        )
        step = SimpleNamespace(
            id=uuid.uuid4(),
            name="Chờ phê duyệt: restart_service",
            status="waiting_approval",
        )

        class FakeRows:
            def all(self):
                return [(approval, tool_call, step)]

        class FakeDb:
            execute_count = 0
            committed = False

            def execute(self, statement):
                self.statement = statement
                self.execute_count += 1
                return FakeRows()

            def commit(self):
                self.committed = True

        db = FakeDb()

        rows = ApprovalRepository.get_pending_request_details(db)

        self.assertEqual([(approval, tool_call, step)], rows)
        self.assertEqual(1, db.execute_count)
        self.assertFalse(db.committed)
        statement_sql = str(db.statement).lower()
        self.assertIn("join tool_calls", statement_sql)
        self.assertIn("join run_steps", statement_sql)

    def test_pending_approval_service_formats_joined_rows(self) -> None:
        from app.services.approval import ApprovalService

        requested_at = datetime.now(UTC)
        expires_at = requested_at + timedelta(minutes=30)
        approval = SimpleNamespace(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            status="pending",
            requested_at=requested_at,
            expires_at=expires_at,
            resolved_at=None,
        )
        tool_call = SimpleNamespace(
            skill_name="restart_service",
            arguments_json={"node_name": "node-1", "service": "mme"},
            connector_name="ssh",
            risk_level="require_approval",
        )
        step = SimpleNamespace(
            id=uuid.uuid4(),
            name="Chờ phê duyệt: restart_service",
            status="waiting_approval",
        )

        class FakeDb:
            def execute(self, statement):
                class FakeRows:
                    def all(self):
                        return [(approval, tool_call, step)]

                return FakeRows()

        payload = ApprovalService.list_pending_approval_details_for_ui(FakeDb())

        self.assertEqual(1, len(payload))
        self.assertEqual(str(approval.id), payload[0]["approval_id"])
        self.assertEqual(str(approval.run_id), payload[0]["run_id"])
        self.assertEqual("pending", payload[0]["status"])
        self.assertEqual("restart_service", payload[0]["skill_details"]["skill_name"])
        self.assertEqual("waiting_approval", payload[0]["timeline_step"]["status"])


if __name__ == "__main__":
    unittest.main()
