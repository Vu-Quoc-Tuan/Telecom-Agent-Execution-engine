import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database.connection import Base


class ToolCall(Base):
    __tablename__ = "tool_calls"

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

    run_step_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "run_steps.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )

    # ID tool call do OpenAI/Anthropic trả về.
    provider_tool_call_id = Column(
        String(255),
        nullable=True,
    )

    skill_name = Column(
        String(100),
        nullable=False,
    )

    # ssh | clickhouse | external_postgres | internal | mcp_server_name
    connector_name = Column(
        String(100),
        nullable=True,
    )

    arguments_json = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # Chưa chạy hoặc thất bại trước khi có kết quả thì có thể NULL.
    result_json = Column(
        JSONB,
        nullable=True,
    )

    output_truncated = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    risk_level = Column(
        String(30),
        nullable=False,
        default="auto_execute",
        server_default=text("'auto_execute'"),
    )

    # auto_execute | require_approval
    requires_approval = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    status = Column(
        String(30),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    # pending | waiting_approval | running | completed
    # failed | rejected | cancelled | timed_out

    latency_ms = Column(
        Integer,
        nullable=True,
    )

    error_message = Column(
        Text,
        nullable=True,
    )

    # Ngăn action bị thực thi lặp khi retry.
    idempotency_key = Column(
        String(150),
        nullable=True,
        unique=True,
    )

    # Thời điểm bản ghi tool call được tạo.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Thời điểm record được cập nhật gần nhất.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Chỉ gán khi tool thực sự bắt đầu chạy.
    started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Chỉ gán khi completed/failed/rejected/cancelled/timed_out.
    completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    agent_run = relationship(
        "AgentRun",
        back_populates="tool_calls",
    )

    run_step = relationship(
        "RunStep",
        back_populates="tool_call",
    )

    approval_request = relationship(
        "ApprovalRequest",
        back_populates="tool_call",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            """
            risk_level IN (
                'auto_execute',
                'require_approval'
            )
            """,
            name="ck_tool_calls_risk_level",
        ),
        CheckConstraint(
            """
            status IN (
                'pending',
                'waiting_approval',
                'running',
                'completed',
                'failed',
                'rejected',
                'cancelled',
                'timed_out'
            )
            """,
            name="ck_tool_calls_status",
        ),
        Index(
            "idx_tool_calls_run_id",
            "run_id",
        ),
        Index(
            "idx_tool_calls_skill_name",
            "skill_name",
        ),
    )
