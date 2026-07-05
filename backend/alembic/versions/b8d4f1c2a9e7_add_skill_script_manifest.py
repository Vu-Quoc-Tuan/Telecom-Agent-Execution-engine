"""add skill script manifest

Revision ID: b8d4f1c2a9e7
Revises: a7b8c9d0e1f2
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "b8d4f1c2a9e7"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS "
            "script_manifest JSONB DEFAULT '{}'::jsonb NOT NULL"
        )
        return

    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("skills")}
    if "script_manifest" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "script_manifest",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS script_manifest")
        return

    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("skills")}
    if "script_manifest" in columns:
        op.drop_column("skills", "script_manifest")
