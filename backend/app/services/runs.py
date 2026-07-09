from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.common.enums import InterventionStatus, RunStatus
from app.database.models.agent_runs import AgentRun
from app.database.models.chat_messages import ChatMessage
from app.database.repositories.approvals import ApprovalRepository
from app.database.repositories.audit_logs import AuditLogRepository
from app.database.repositories.messages import MessageRepository
from app.database.repositories.run_steps import RunStepRepository
from app.database.repositories.runs import RunRepository
from app.database.repositories.tool_calls import ToolCallRepository


@dataclass(frozen=True)
class RunLifecycleTransition:
    run: AgentRun | None
    changed: bool
    message: str


class RunLifecycleService:
    @staticmethod
    def queue_intervention(
        *,
        db: Session,
        run_id: uuid.UUID,
        content: str,
        requested_by: str,
    ) -> ChatMessage | None:
        run = RunRepository.get_run(db, run_id)
        if run is None or not RunRepository.is_active_status(run.status):
            return None

        return MessageRepository.save_message(
            db=db,
            session_id=run.session_id,
            run_id=run_id,
            role="user",
            content=content,
            metadata={
                "kind": "operator_intervention",
                "intervention_status": InterventionStatus.PENDING,
                "requested_by": requested_by,
            },
        )

    @staticmethod
    def cancel_run(
        *,
        db: Session,
        run_id: uuid.UUID,
        requested_by: str,
        reason: str | None = None,
    ) -> RunLifecycleTransition:
        run = RunRepository.get_run(db, run_id)
        if run is None:
            return RunLifecycleTransition(run=None, changed=False, message="Run does not exist.")

        if run.status == RunStatus.CANCELLED.value:
            return RunLifecycleTransition(
                run=run,
                changed=False,
                message="Run was already cancelled.",
            )
        if RunRepository.is_terminal_status(run.status):
            return RunLifecycleTransition(
                run=run,
                changed=False,
                message=f"Run is already terminal with status '{run.status}'.",
            )

        message = reason or "Run cancelled by operator."
        run = RunRepository.update_run_status(
            db,
            run_id,
            status=RunStatus.CANCELLED.value,
            error_msg=message,
            commit=False,
        )
        if run is None or run.status != RunStatus.CANCELLED.value:
            return RunLifecycleTransition(
                run=run,
                changed=False,
                message=f"Run is already terminal with status '{run.status if run else 'missing'}'.",
            )
        RunStepRepository.close_open_steps_by_run(
            db,
            run_id=run_id,
            status=RunStatus.CANCELLED.value,
            summary=message,
            commit=False,
        )
        ToolCallRepository.close_open_tool_calls_by_run(
            db,
            run_id=run_id,
            status=RunStatus.CANCELLED.value,
            output=message,
            commit=False,
        )
        ApprovalRepository.cancel_pending_by_run(
            db,
            run_id=run_id,
            commit=False,
        )
        AuditLogRepository.log_event(
            db,
            actor_type="user",
            actor_id=requested_by,
            action="run.cancelled",
            session_id=run.session_id if run else None,
            run_id=run_id,
            resource_type="agent_run",
            resource_id=str(run_id),
            details={"reason": message},
            commit=False,
        )
        db.commit()
        return RunLifecycleTransition(run=run, changed=True, message=message)

    @staticmethod
    def mark_timed_out_runs(
        *,
        db: Session,
        timeout_seconds: int,
        limit: int = 100,
        now: datetime | None = None,
    ) -> list[AgentRun]:
        reference_time = now or datetime.now(UTC)
        cutoff = reference_time - timedelta(seconds=timeout_seconds)
        stale_runs = RunRepository.list_stale_active_runs(db, cutoff=cutoff, limit=limit)
        timed_out_runs: list[AgentRun] = []

        for run in stale_runs:
            if RunRepository.is_terminal_status(run.status):
                continue
            message = f"Run timed out after {timeout_seconds} seconds without progress."
            updated_run = RunRepository.update_run_status(
                db,
                run.id,
                status=RunStatus.TIMED_OUT.value,
                error_msg=message,
                commit=False,
            )
            if updated_run is None or updated_run.status != RunStatus.TIMED_OUT.value:
                continue
            RunStepRepository.close_open_steps_by_run(
                db,
                run_id=run.id,
                status=RunStatus.TIMED_OUT.value,
                summary=message,
                commit=False,
            )
            ToolCallRepository.close_open_tool_calls_by_run(
                db,
                run_id=run.id,
                status=RunStatus.TIMED_OUT.value,
                output=message,
                commit=False,
            )
            ApprovalRepository.cancel_pending_by_run(
                db,
                run_id=run.id,
                commit=False,
            )
            AuditLogRepository.log_event(
                db,
                actor_type="system",
                actor_id="timeout_sweeper",
                action="run.timed_out",
                session_id=run.session_id,
                run_id=run.id,
                resource_type="agent_run",
                resource_id=str(run.id),
                details={"timeout_seconds": timeout_seconds, "cutoff": cutoff.isoformat()},
                commit=False,
            )
            db.commit()
            timed_out_runs.append(updated_run)

        return timed_out_runs
