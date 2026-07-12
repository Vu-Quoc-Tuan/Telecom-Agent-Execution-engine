from __future__ import annotations

import types
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from app.common.enums import RunStatus


class FakeDb:
    def __init__(self, run=None):
        self.run = run
        self.commits = 0
        self.refreshes = 0

    def get(self, model, run_id):
        return self.run

    def scalar(self, statement):
        self.statement = statement
        return self.run

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, run) -> None:
        self.refreshes += 1


class FakeCreateRunDb:
    def __init__(self):
        self.run = None
        self.commits = 0
        self.refreshes = 0

    def add(self, run) -> None:
        self.run = run

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, run) -> None:
        self.refreshes += 1


class RunRepositoryLifecycleTests(unittest.TestCase):
    def test_create_run_records_prompt_version_and_started_at(self) -> None:
        from app.database.repositories.runs import RunRepository

        db = FakeCreateRunDb()

        run = RunRepository.create_run(
            db,
            session_id=uuid.uuid4(),
            provider="openai",
            model="gpt-4o",
            prompt_version="prompt-v2",
        )

        self.assertIs(run, db.run)
        self.assertEqual("prompt-v2", run.prompt_version)
        self.assertIsNotNone(run.started_at)
        self.assertEqual(UTC, run.started_at.tzinfo)

    def test_terminal_run_status_is_not_overwritten(self) -> None:
        from app.database.repositories.runs import RunRepository

        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            status=RunStatus.CANCELLED.value,
            completed_at=datetime.now(UTC),
            error_message="Cancelled by operator.",
        )

        updated = RunRepository.update_run_status(
            FakeDb(run),
            run.id,
            status=RunStatus.COMPLETED.value,
        )

        self.assertIs(updated, run)
        self.assertEqual(RunStatus.CANCELLED.value, run.status)
        self.assertEqual("Cancelled by operator.", run.error_message)

    def test_repeated_terminal_status_preserves_completed_at(self) -> None:
        from app.database.repositories.runs import RunRepository

        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            status=RunStatus.COMPLETED.value,
            completed_at=datetime.now(UTC),
            error_message=None,
        )
        db = FakeDb(run)

        RunRepository.update_run_status(
            db,
            run.id,
            status=RunStatus.COMPLETED.value,
        )

        sql = str(db.statement.compile(dialect=postgresql.dialect()))
        self.assertIn("coalesce(agent_runs.completed_at, now())", sql.lower())


class RunLifecycleServiceTests(unittest.TestCase):
    def test_cancel_run_marks_run_and_open_child_records_cancelled(self) -> None:
        from app.services.runs import RunLifecycleService

        run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            status=RunStatus.RUNNING.value,
            completed_at=None,
            error_message=None,
        )

        def update_run_status(db, run_id, status, error_msg=None, commit=True):
            run.status = status
            run.error_message = error_msg
            run.completed_at = datetime.now(UTC)
            return run

        with (
            patch("app.services.runs.RunRepository.get_run", return_value=run),
            patch(
                "app.services.runs.RunRepository.update_run_status",
                side_effect=update_run_status,
            ) as update_status,
            patch(
                "app.services.runs.RunStepRepository.close_open_steps_by_run",
                return_value=2,
            ) as close_steps,
            patch(
                "app.services.runs.ToolCallRepository.close_open_tool_calls_by_run",
                return_value=1,
            ) as close_tools,
            patch(
                "app.services.runs.ApprovalRepository.cancel_pending_by_run",
                return_value=1,
            ) as cancel_approvals,
        ):
            db = FakeDb()
            result = RunLifecycleService.cancel_run(
                db=db,
                run_id=run.id,
                requested_by="operator_admin",
                reason="User stopped this run.",
            )

        self.assertTrue(result.changed)
        self.assertEqual(RunStatus.CANCELLED.value, result.run.status)
        update_status.assert_called_once()
        self.assertEqual(RunStatus.CANCELLED.value, update_status.call_args.kwargs["status"])
        close_steps.assert_called_once()
        self.assertEqual(RunStatus.CANCELLED.value, close_steps.call_args.kwargs["status"])
        close_tools.assert_called_once()
        self.assertEqual(RunStatus.CANCELLED.value, close_tools.call_args.kwargs["status"])
        cancel_approvals.assert_called_once()
        self.assertFalse(update_status.call_args.kwargs["commit"])
        self.assertFalse(close_steps.call_args.kwargs["commit"])
        self.assertFalse(close_tools.call_args.kwargs["commit"])
        self.assertFalse(cancel_approvals.call_args.kwargs["commit"])
        self.assertEqual(1, db.commits)

    def test_timeout_sweep_marks_stale_active_runs_timed_out(self) -> None:
        from app.services.runs import RunLifecycleService

        now = datetime.now(UTC)
        stale_run = types.SimpleNamespace(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            status=RunStatus.RUNNING.value,
            updated_at=now - timedelta(seconds=3601),
            completed_at=None,
            error_message=None,
        )

        def update_run_status(db, run_id, status, error_msg=None, commit=True):
            stale_run.status = status
            stale_run.error_message = error_msg
            stale_run.completed_at = now
            return stale_run

        with (
            patch(
                "app.services.runs.RunRepository.list_stale_active_runs", return_value=[stale_run]
            ),
            patch(
                "app.services.runs.RunRepository.update_run_status",
                side_effect=update_run_status,
            ) as update_status,
            patch("app.services.runs.RunStepRepository.close_open_steps_by_run") as close_steps,
            patch(
                "app.services.runs.ToolCallRepository.close_open_tool_calls_by_run"
            ) as close_tools,
            patch("app.services.runs.ApprovalRepository.cancel_pending_by_run") as cancel_approvals,
        ):
            results = RunLifecycleService.mark_timed_out_runs(
                db=FakeDb(),
                timeout_seconds=3600,
                limit=100,
                now=now,
            )

        self.assertEqual([stale_run], results)
        self.assertEqual(RunStatus.TIMED_OUT.value, stale_run.status)
        self.assertIn("3600", stale_run.error_message)
        update_status.assert_called_once()
        self.assertEqual(RunStatus.TIMED_OUT.value, update_status.call_args.kwargs["status"])
        close_steps.assert_called_once()
        close_tools.assert_called_once()
        cancel_approvals.assert_called_once()


class ChildLifecycleRepositoryTests(unittest.TestCase):
    def test_cancelled_tool_call_cannot_be_started_again(self) -> None:
        from app.database.repositories.tool_calls import ToolCallRepository

        tool_call = types.SimpleNamespace(
            id=uuid.uuid4(),
            status=RunStatus.CANCELLED.value,
            started_at=None,
        )

        result = ToolCallRepository.start_execution(FakeDb(tool_call), tool_call.id)

        self.assertIs(result, tool_call)
        self.assertEqual(RunStatus.CANCELLED.value, tool_call.status)
        self.assertIsNone(tool_call.started_at)

    def test_late_tool_result_does_not_overwrite_cancelled_status(self) -> None:
        from app.database.repositories.tool_calls import ToolCallRepository

        tool_call = types.SimpleNamespace(
            id=uuid.uuid4(),
            status=RunStatus.CANCELLED.value,
            result_json={"output": "Run cancelled by operator."},
            latency_ms=None,
            error_message="Run cancelled by operator.",
            output_truncated=False,
            completed_at=datetime.now(UTC),
        )
        db = FakeDb(tool_call)

        result = ToolCallRepository.save_result(
            db,
            tool_call.id,
            status="completed",
            result={"output": "late success"},
            latency_ms=500,
        )

        self.assertIs(result, tool_call)
        self.assertEqual(RunStatus.CANCELLED.value, tool_call.status)
        self.assertEqual({"output": "Run cancelled by operator."}, tool_call.result_json)

    def test_late_step_result_does_not_overwrite_timed_out_status(self) -> None:
        from app.database.repositories.run_steps import RunStepRepository

        step = types.SimpleNamespace(
            id=uuid.uuid4(),
            status=RunStatus.TIMED_OUT.value,
            summary="Run timed out.",
            metadata_json={},
            completed_at=datetime.now(UTC),
        )
        db = FakeDb(step)

        result = RunStepRepository.complete_step(
            db,
            step.id,
            status="completed",
            summary="late success",
        )

        self.assertIs(result, step)
        self.assertEqual(RunStatus.TIMED_OUT.value, step.status)
        self.assertEqual("Run timed out.", step.summary)

    def test_timed_out_step_cannot_be_started_again(self) -> None:
        from app.database.repositories.run_steps import RunStepRepository

        step = types.SimpleNamespace(
            id=uuid.uuid4(),
            status=RunStatus.TIMED_OUT.value,
            started_at=None,
        )

        result = RunStepRepository.start_step(FakeDb(step), step.id)

        self.assertIs(result, step)
        self.assertEqual(RunStatus.TIMED_OUT.value, step.status)
        self.assertIsNone(step.started_at)


if __name__ == "__main__":
    unittest.main()
