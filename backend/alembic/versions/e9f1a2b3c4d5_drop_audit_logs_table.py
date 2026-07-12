"""Drop unused audit_logs table.

Revision ID: e9f1a2b3c4d5
Revises: c2eb5156b73c
Create Date: 2026-07-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f1a2b3c4d5"
down_revision: str | None = "c2eb5156b73c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("idx_audit_logs_session_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_run_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_action_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")


def downgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'system')",
            name="ck_audit_logs_actor_type",
        ),
    )
    op.create_index("idx_audit_logs_action_created_at", "audit_logs", ["action", "created_at"])
    op.create_index("idx_audit_logs_run_created_at", "audit_logs", ["run_id", "created_at"])
    op.create_index(
        "idx_audit_logs_session_created_at", "audit_logs", ["session_id", "created_at"]
    )
