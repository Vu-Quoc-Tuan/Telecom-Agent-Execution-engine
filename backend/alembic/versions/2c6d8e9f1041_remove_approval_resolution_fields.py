"""remove_approval_resolution_fields

Revision ID: 2c6d8e9f1041
Revises: 7aa32ba8f629
Create Date: 2026-06-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c6d8e9f1041"
down_revision: str | None = "7aa32ba8f629"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("approval_requests", "resolution_note")
    op.drop_column("approval_requests", "resolved_by")
    op.drop_column("approval_requests", "reason")


def downgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
            server_default="Legacy approval reason unavailable.",
        ),
    )
    op.alter_column("approval_requests", "reason", server_default=None)
    op.add_column(
        "approval_requests",
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "approval_requests",
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )
