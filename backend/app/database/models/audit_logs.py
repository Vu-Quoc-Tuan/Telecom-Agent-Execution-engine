import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database.connection import Base


class AuditLog(Base):
    """Append-only security record that survives deletion of domain entities."""

    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    run_id = Column(UUID(as_uuid=True), nullable=True)

    # the actor that performs the action
    actor_type = Column(String(20), nullable=False)
    actor_id = Column(String(100), nullable=True)
    action = Column(String(100), nullable=False)

    # Table name or other object that was affected
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(String(100), nullable=True)
    details_json = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'agent', 'system')",
            name="ck_audit_logs_actor_type",
        ),
        Index("idx_audit_logs_action_created_at", "action", "created_at"),
        Index("idx_audit_logs_run_created_at", "run_id", "created_at"),
        Index("idx_audit_logs_session_created_at", "session_id", "created_at"),
    )
