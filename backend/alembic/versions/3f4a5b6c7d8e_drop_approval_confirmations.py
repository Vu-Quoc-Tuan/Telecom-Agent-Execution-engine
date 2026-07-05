"""drop_approval_confirmations

Revision ID: 3f4a5b6c7d8e
Revises: 2c6d8e9f1041
Create Date: 2026-06-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3f4a5b6c7d8e"
down_revision: str | None = "2c6d8e9f1041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_approval_requests_confirmation_count",
        "approval_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_approval_requests_required_confirmations",
        "approval_requests",
        type_="check",
    )
    op.drop_column("approval_requests", "confirmation_count")
    op.drop_column("approval_requests", "required_confirmations")


def downgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column(
            "required_confirmations",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "approval_requests",
        sa.Column(
            "confirmation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_approval_requests_required_confirmations",
        "approval_requests",
        "required_confirmations IN (1, 2)",
    )
    op.create_check_constraint(
        "ck_approval_requests_confirmation_count",
        "approval_requests",
        "confirmation_count >= 0 AND confirmation_count <= required_confirmations",
    )
