import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database.connection import Base


class Skill(Base):
    __tablename__ = "skills"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name = Column(
        String(100),
        nullable=False,
        unique=True,
    )

    description = Column(
        Text,
        nullable=False,
    )

    skill_md = Column(
        Text,
        nullable=False,
    )

    # Toàn bộ YAML frontmatter Agent Skills đã parse.
    frontmatter = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # Map path tương đối -> {encoding, content, media_type, size} (read_skill_file - L3).
    bundled_files = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    script_manifest = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # uploaded | testing | ready | rejected
    status = Column(
        String(30),
        nullable=False,
        default="uploaded",
        server_default=text("'uploaded'"),
    )

    # Tầng kiểm tra an ninh
    security_review_log = Column(
        Text,
        nullable=True,
    )

    is_malicious = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
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

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'testing', 'ready', 'rejected')",
            name="ck_skills_status",
        ),
        Index(
            "idx_skills_status_ready",
            "status",
            postgresql_where=text("status = 'ready'"),
        ),
    )
