"""tighten run step types

Revision ID: f2a6c0d9b8e1
Revises: 995be2700764
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "f2a6c0d9b8e1"
down_revision: str | None = "995be2700764"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_STEP_TYPES = "'llm_call', 'tool_call', 'approval', 'error'"
LEGACY_STEP_TYPES = (
    "'request_received', 'llm_call', 'tool_call', "
    "'analysis_summary', 'approval', 'final_answer', 'error'"
)


def _replace_step_type_constraint(allowed_values: str) -> None:
    if context.is_offline_mode():
        op.execute("ALTER TABLE run_steps DROP CONSTRAINT IF EXISTS ck_run_steps_step_type")
        op.execute(
            "ALTER TABLE run_steps ADD CONSTRAINT ck_run_steps_step_type "
            f"CHECK (step_type IN ({allowed_values}))"
        )
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraint_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("run_steps")
    }
    if "ck_run_steps_step_type" in constraint_names:
        op.drop_constraint("ck_run_steps_step_type", "run_steps", type_="check")
    op.create_check_constraint(
        "ck_run_steps_step_type",
        "run_steps",
        f"step_type IN ({allowed_values})",
    )


def upgrade() -> None:
    _replace_step_type_constraint(ACTIVE_STEP_TYPES)


def downgrade() -> None:
    _replace_step_type_constraint(LEGACY_STEP_TYPES)
