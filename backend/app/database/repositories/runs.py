import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.common.enums import RunStatus
from app.database.models.agent_runs import AgentRun

_TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
    RunStatus.TIMED_OUT.value,
}

_ACTIVE_RUN_STATUSES = {
    RunStatus.PENDING.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_APPROVAL.value,
}


class RunRepository:
    @staticmethod
    def is_terminal_status(status: str) -> bool:
        return status in _TERMINAL_RUN_STATUSES

    @staticmethod
    def is_active_status(status: str) -> bool:
        return status in _ACTIVE_RUN_STATUSES

    @staticmethod
    def create_run(
        db: Session,
        session_id: uuid.UUID,
        provider: str,
        model: str,
        config_dict: dict = None,
        prompt_version: str = "0.0.1",
    ) -> AgentRun:
        """
        Create a new AgentRun with the given session_id, provider, model, and optional configuration dictionary.
        """
        if config_dict is None:
            config_dict = {"temperature": 0.1, "max_steps": 10, "tool_timeout_seconds": 30}

        new_run = AgentRun(
            id=uuid.uuid4(),
            session_id=session_id,
            provider=provider,
            model=model,
            status=RunStatus.RUNNING.value,
            prompt_version=prompt_version,
            run_config_json=config_dict,
            step_count=0,
            started_at=datetime.now(UTC),
        )
        db.add(new_run)
        db.commit()
        db.refresh(new_run)
        return new_run

    @staticmethod
    def get_run(db: Session, run_id: uuid.UUID) -> AgentRun | None:
        """
        Get the details of a specific AgentRun.
        """
        return db.get(AgentRun, run_id)

    @staticmethod
    def update_run_status(
        db: Session,
        run_id: uuid.UUID,
        status: str,
        error_msg: str | None = None,
        commit: bool = True,
    ) -> AgentRun | None:
        """
        Update the status of an AgentRun (completed, failed, waiting_approval,...)
        """
        values = {"status": status}
        if status in _TERMINAL_RUN_STATUSES:
            values["completed_at"] = func.now()
        if error_msg is not None:
            values["error_message"] = error_msg
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                or_(
                    AgentRun.status.not_in(_TERMINAL_RUN_STATUSES),
                    AgentRun.status == status,
                ),
            )
            .values(**values)
            .returning(AgentRun)
            .execution_options(populate_existing=True)
        )
        run = db.scalar(statement)
        if run is None:
            run = db.get(AgentRun, run_id)
        if commit:
            db.commit()
            if run is not None:
                db.refresh(run)
        return run

    @staticmethod
    def list_stale_active_runs(
        db: Session,
        cutoff: datetime,
        limit: int = 100,
    ) -> list[AgentRun]:
        statement = (
            select(AgentRun)
            .where(
                AgentRun.status.in_(_ACTIVE_RUN_STATUSES),
                AgentRun.updated_at <= cutoff,
            )
            .order_by(AgentRun.updated_at.asc())
            .limit(limit)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def attach_langfuse_trace(
        db: Session, run_id: uuid.UUID, trace_id: str, trace_url: str
    ) -> AgentRun | None:
        """
        Attach Langfuse trace information to an AgentRun for frontend link rendering.
        """
        run = db.get(AgentRun, run_id)
        if run:
            run.langfuse_trace_id = trace_id
            run.langfuse_trace_url = trace_url
            db.commit()
            db.refresh(run)
        return run

    @staticmethod
    def increment_step_count(db: Session, run_id: uuid.UUID) -> AgentRun | None:
        """
        Increment the step count of an AgentRun.
        """
        run = db.get(AgentRun, run_id)
        if run:
            run.step_count += 1
            db.commit()
            db.refresh(run)
        return run
