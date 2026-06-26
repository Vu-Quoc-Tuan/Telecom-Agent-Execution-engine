# backend/app/services/session.py
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database.models.session import Session as SessionModel
from app.database.repositories.audit_logs import AuditLogRepository
from app.database.repositories.sessions import SessionRepository


class SessionService:
    @staticmethod
    def create_new_session(db: Session, title: str) -> SessionModel:
        """Kích hoạt đẻ một phiên chat mới và lưu vết an ninh hệ thống"""
        # 1. Gọi Repo ghi nhận vào Postgres
        session = SessionRepository.create_session(db, title)

        # 2. Ghi một bản ghi nhật ký an ninh hệ thống (Audit Log)
        AuditLogRepository.log_event(
            db=db,
            actor_type="user",
            actor_id="operator",  # Tạm thời fix, sau này bốc từ jwt token
            action="session.created",
            session_id=session.id,
            resource_type="session",
            resource_id=str(session.id),
            details={"title": title},
        )
        return session

    @staticmethod
    def get_active_session(db: Session, session_id: uuid.UUID) -> SessionModel | None:
        """Lấy chi tiết một phiên chat, tự động bẫy lỗi nếu phiên đó đã bị xóa mềm"""
        return SessionRepository.get_session_by_id(db, session_id)

    @staticmethod
    def list_user_sessions(db: Session, limit: int = 50) -> list[SessionModel]:
        """Tải toàn bộ danh sách các cuộc chat chưa bị xóa mềm lên Sidebar trái UI"""
        return SessionRepository.list_active_sessions(db, limit)

    @staticmethod
    def soft_delete_session(db: Session, session_id: uuid.UUID) -> bool:
        """Thực hiện xóa mềm phiên chat để dọn sạch UI nhưng bảo toàn log hệ thống"""
        # Kiểm tra xem session có tồn tại thực tế không
        session = SessionRepository.get_session_by_id(db, session_id)
        if not session:
            return False

        # Kích hoạt gắn cờ deleted_at qua Repo
        success = SessionRepository.delete_session(db, session_id)
        if success:
            # Ghi vết vào nhật ký audit để phục vụ hậu kiểm an ninh viễn thông
            AuditLogRepository.log_event(
                db=db,
                actor_type="user",
                actor_id="operator",
                action="session.deleted",
                session_id=session_id,
                resource_type="session",
                resource_id=str(session_id),
                details={"title": session.title, "mode": "soft_delete"},
            )
        return success
