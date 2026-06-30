from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.session import Session as SessionModel


class SessionRepository:
    @staticmethod
    def create_session(db: Session, title: str) -> SessionModel:
        session = SessionModel(
            id=uuid.uuid4(),
            title=title.strip(),
            status="active",
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def get_session_by_id(
        db: Session,
        session_id: uuid.UUID,
    ) -> SessionModel | None:
        statement = select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.deleted_at.is_(None),
        )
        return db.scalar(statement)

    @staticmethod
    def list_active_sessions(
        db: Session,
        limit: int = 50,
    ) -> list[SessionModel]:
        statement = (
            select(SessionModel)
            .where(
                SessionModel.status == "active",
                SessionModel.deleted_at.is_(None),
            )
            .order_by(SessionModel.updated_at.desc())
            .limit(limit)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def delete_session(db: Session, session_id: uuid.UUID) -> bool:
        session = db.get(SessionModel, session_id)
        if session is None or session.deleted_at is not None:
            return False
        session.status = "archived"
        session.deleted_at = datetime.now(UTC)
        db.commit()
        return True
