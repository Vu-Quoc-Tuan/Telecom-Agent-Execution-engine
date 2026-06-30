from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.common.enums import StepType
from app.database.models.run_steps import RunStep

_OPEN_STEP_STATUSES = {"pending", "running", "waiting_approval"}


class RunStepRepository:
    @staticmethod
    def create_step(
        db: Session,
        run_id: uuid.UUID,
        step_index: int,
        step_type: str,
        name: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> RunStep:
        step = RunStep(
            id=uuid.uuid4(),
            run_id=run_id,
            step_index=step_index,
            step_type=step_type,
            name=name,
            summary=summary,
            metadata_json=metadata or {},
            status=status,
        )
        db.add(step)
        db.commit()
        db.refresh(step)
        return step

    @staticmethod
    def create_error_step(
        db: Session,
        run_id: uuid.UUID,
        summary: str,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> RunStep:
        max_index = db.scalar(select(func.max(RunStep.step_index)).where(RunStep.run_id == run_id))
        now = datetime.now(UTC)
        step = RunStep(
            id=uuid.uuid4(),
            run_id=run_id,
            step_index=0 if max_index is None else max_index + 1,
            step_type=StepType.ERROR.value,
            name="Run Error",
            summary=summary,
            status="failed",
            metadata_json=metadata or {},
            started_at=now,
            completed_at=now,
        )
        db.add(step)
        if commit:
            db.commit()
            db.refresh(step)
        else:
            db.flush()
        return step

    @staticmethod
    def start_step(db: Session, step_id: uuid.UUID) -> RunStep | None:
        step = db.get(RunStep, step_id)
        if step is None:
            return None
        step.status = "running"
        step.started_at = datetime.now(UTC)
        db.commit()
        db.refresh(step)
        return step

    @staticmethod
    def complete_step(
        db: Session,
        step_id: uuid.UUID,
        status: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> RunStep | None:
        step = db.get(RunStep, step_id)
        if step is None:
            return None
        step.status = status
        step.summary = summary
        if metadata is not None:
            step.metadata_json = {**(step.metadata_json or {}), **metadata}
        step.completed_at = datetime.now(UTC)
        if commit:
            db.commit()
            db.refresh(step)
        else:
            db.flush()
        return step

    @staticmethod
    def get_step(db: Session, step_id: uuid.UUID) -> RunStep | None:
        return db.get(RunStep, step_id)

    @staticmethod
    def get_steps_by_run(db: Session, run_id: uuid.UUID) -> list[RunStep]:
        statement = (
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_index.asc())
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def close_open_steps_by_run(
        db: Session,
        run_id: uuid.UUID,
        status: str,
        summary: str,
        commit: bool = True,
    ) -> int:
        now = datetime.now(UTC)
        result = db.execute(
            update(RunStep)
            .where(
                RunStep.run_id == run_id,
                RunStep.status.in_(_OPEN_STEP_STATUSES),
            )
            .values(
                status=status,
                summary=summary,
                completed_at=now,
                updated_at=now,
            )
        )
        if commit:
            db.commit()
        return result.rowcount or 0
