from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.database.models.audit_logs import AuditLog


class AuditLogRepository:
    @staticmethod
    def log_event(
        db: Session,
        actor_type: str,
        action: str,
        actor_id: str | None = None,
        session_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> AuditLog:
        entry = AuditLog(
            id=uuid.uuid4(),
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            session_id=session_id,
            run_id=run_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details_json=details or {},
        )
        db.add(entry)
        if commit:
            db.commit()
            db.refresh(entry)
        return entry
