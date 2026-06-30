"""add execution modes and multi-confirmation approvals

Revision ID: a7b8c9d0e1f2
Revises: d5e1a0c7b9f2
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "d5e1a0c7b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_constraint_if_present(table: str, name: str) -> None:
    if context.is_offline_mode():
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        return
    names = {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(table)
    }
    if name in names:
        op.drop_constraint(name, table, type_="check")


def upgrade() -> None:
    _drop_constraint_if_present("tool_calls", "ck_tool_calls_risk_level")
    op.execute(
        """
        UPDATE tool_calls
        SET risk_level = CASE
            WHEN risk_level IN ('read_only', 'safe_action') THEN 'auto_execute'
            ELSE 'require_approval'
        END
        """
    )
    op.alter_column(
        "tool_calls",
        "risk_level",
        server_default=sa.text("'auto_execute'"),
        existing_type=sa.String(length=30),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_tool_calls_risk_level",
        "tool_calls",
        "risk_level IN ('auto_execute', 'require_approval')",
    )

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


def downgrade() -> None:
    _drop_constraint_if_present(
        "approval_requests", "ck_approval_requests_confirmation_count"
    )
    _drop_constraint_if_present(
        "approval_requests", "ck_approval_requests_required_confirmations"
    )
    op.drop_column("approval_requests", "confirmation_count")
    op.drop_column("approval_requests", "required_confirmations")

    _drop_constraint_if_present("tool_calls", "ck_tool_calls_risk_level")
    op.execute(
        """
        UPDATE tool_calls
        SET risk_level = CASE
            WHEN risk_level = 'auto_execute' THEN 'read_only'
            ELSE 'dangerous_action'
        END
        """
    )
    op.alter_column(
        "tool_calls",
        "risk_level",
        server_default=sa.text("'read_only'"),
        existing_type=sa.String(length=30),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_tool_calls_risk_level",
        "tool_calls",
        "risk_level IN ('read_only', 'safe_action', 'dangerous_action', 'prohibited')",
    )
