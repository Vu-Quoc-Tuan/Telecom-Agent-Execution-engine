import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database.connection import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),  # xóa lan truyền
        nullable=False,
    )

    provider = Column(
        String(50),
        nullable=False,
        default="openai",
        server_default=text("'openai'"),
    )

    model = Column(
        String(100),
        nullable=False,
        default="gpt-4o",
        server_default=text("'gpt-4o'"),
    )

    # pending | running | waiting_approval |completed | failed | cancelled | timed_out
    status = Column(
        String(30),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )

    prompt_version = Column(
        String(50),
        nullable=False,
        default="0.0.1",
        server_default=text("'0.0.1'"),
    )

    run_config_json = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    step_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("'0'::integer"),
    )

    error_message = Column(
        Text,
        nullable=True,
    )

    # Tạo lúc nào
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Cập nhật gần nhất
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Bắt đầu chạy lúc nào
    started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Kết thúc, lỗi, timeout hoặc bị hủy lúc nào
    completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    session = relationship(
        "Session",
        back_populates="agent_runs",
    )

    run_steps = relationship(
        "RunStep",
        back_populates="agent_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    tool_calls = relationship(
        "ToolCall",
        back_populates="agent_run",
        passive_deletes=True,
    )

    # ChatMessage.run_id dùng ON DELETE SET NULL,
    chat_messages = relationship(
        "ChatMessage",
        back_populates="agent_run",
        passive_deletes=True,
    )

    approval_requests = relationship(
        "ApprovalRequest",
        back_populates="agent_run",
        passive_deletes=True,
    )

    __table_args__ = (
        Index(
            "idx_agent_runs_session_created_at",
            "session_id",
            "created_at",
        ),
        Index(
            "idx_agent_runs_status",
            "status",
        ),
        CheckConstraint(
            """
            status IN (
                'pending',
                'running',
                'waiting_approval',
                'completed',
                'failed',
                'cancelled',
                'timed_out'
            )
            """,
            name="ck_agent_runs_status",
        ),
    )
