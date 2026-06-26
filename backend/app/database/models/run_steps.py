import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database.connection import Base


class RunStep(Base):
    __tablename__ = "run_steps"

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

    step_index = Column(
        Integer,
        nullable=False,
    )

    # llm_call | tool_call | approval | error
    step_type = Column(
        String(30),
        nullable=False,
    )

    name = Column(
        String(100),
        nullable=False,
    )

    summary = Column(
        Text,
        nullable=True,
    )

    # pending | running | waiting_approval | completed | failed | cancelled | timed_out
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )

    metadata_json = Column(
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

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # step bắt đầu thực thi.
    started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    agent_run = relationship(
        "AgentRun",
        back_populates="run_steps",
    )

    tool_call = relationship(
        "ToolCall",
        back_populates="run_step",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "step_index",
            name="uq_run_steps_run_step_index",
        ),
        CheckConstraint(
            """
            step_type IN (
                'llm_call',
                'tool_call',
                'approval',
                'error'
            )
            """,
            name="ck_run_steps_step_type",
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
            name="ck_run_steps_status",
        ),
    )
