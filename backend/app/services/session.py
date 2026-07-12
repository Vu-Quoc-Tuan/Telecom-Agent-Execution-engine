from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database.models.session import Session as SessionModel
from app.database.repositories.sessions import SessionRepository


class SessionService:
    @staticmethod
    def create_new_session(db: Session, title: str) -> SessionModel:
        """Create a new chat session."""
        return SessionRepository.create_session(db, title)

    @staticmethod
    def get_active_session(db: Session, session_id: uuid.UUID) -> SessionModel | None:
        """Return a session if it exists and is not soft-deleted."""
        return SessionRepository.get_session_by_id(db, session_id)

    @staticmethod
    def list_user_sessions(db: Session, limit: int = 50) -> list[SessionModel]:
        """List active (non soft-deleted) sessions for the sidebar."""
        return SessionRepository.list_active_sessions(db, limit)

    @staticmethod
    def soft_delete_session(db: Session, session_id: uuid.UUID) -> bool:
        """Soft-delete a session so it disappears from the UI."""
        session = SessionRepository.get_session_by_id(db, session_id)
        if not session:
            return False
        return SessionRepository.delete_session(db, session_id)
