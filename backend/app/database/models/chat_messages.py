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


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_runs.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    # user | assistant | tool
    role = Column(
        String(20),
        nullable=False,
    )

    content = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )

    # pending | streaming | completed | failed
    status = Column(
        String(20),
        nullable=False,
        default="completed",
        server_default=text("'completed'"),
    )

    sequence_no = Column(
        Integer,
        nullable=False,
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

    # Relationships
    session = relationship(
        "Session",
        back_populates="chat_messages",
    )

    agent_run = relationship(
        "AgentRun",
        back_populates="chat_messages",
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence_no",
            name="uq_chat_messages_session_sequence",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant', 'tool')",
            name="ck_chat_messages_role",
        ),
        CheckConstraint(
            """
            status IN (
                'pending',
                'streaming',
                'completed',
                'failed'
            )
            """,
            name="ck_chat_messages_status",
        ),
    )
