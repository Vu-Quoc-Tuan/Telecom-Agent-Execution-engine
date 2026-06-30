import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.connection import Base


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    tool_call_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "tool_calls.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )

    # pending | approved | rejected | expired | cancelled
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )

    requested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Dùng khi approval có thời hạn.
    expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolved_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    required_confirmations = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    confirmation_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    tool_call = relationship(
        "ToolCall",
        back_populates="approval_request",
    )

    agent_run = relationship(
        "AgentRun",
        back_populates="approval_requests",
    )

    __table_args__ = (
        CheckConstraint(
            """
            status IN (
                'pending',
                'approved',
                'rejected',
                'expired',
                'cancelled'
            )
            """,
            name="ck_approval_requests_status",
        ),
        CheckConstraint(
            "required_confirmations IN (1, 2)",
            name="ck_approval_requests_required_confirmations",
        ),
        CheckConstraint(
            "confirmation_count >= 0 AND confirmation_count <= required_confirmations",
            name="ck_approval_requests_confirmation_count",
        ),
        Index(
            "idx_approval_requests_run_id",
            "run_id",
        ),
        Index(
            "idx_approval_requests_pending",
            "requested_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
