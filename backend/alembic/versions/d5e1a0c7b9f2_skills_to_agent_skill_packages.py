"""migrate skills table from python-function model to Agent Skill packages

Revision ID: d5e1a0c7b9f2
Revises: f2a6c0d9b8e1
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d5e1a0c7b9f2"
down_revision: str | None = "f2a6c0d9b8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_COLUMNS = ("code_python", "input_schema", "connector_name", "risk_level")
_OLD_CONSTRAINTS = ("ck_skills_connector_name", "ck_skills_risk_level")
_NEW_COLUMNS = ("skill_md", "frontmatter", "bundled_files")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("skills")}
    constraints = {c["name"] for c in inspector.get_check_constraints("skills")}

    for constraint in _OLD_CONSTRAINTS:
        if constraint in constraints:
            op.drop_constraint(constraint, "skills", type_="check")

    if "skill_md" not in columns:
        op.add_column(
            "skills",
            sa.Column("skill_md", sa.Text(), nullable=False, server_default=sa.text("''")),
        )
    if "frontmatter" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "frontmatter",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    if "bundled_files" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "bundled_files",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )

    if "code_python" in columns:
        op.execute(
            """
            UPDATE skills
            SET status = 'rejected',
                security_review_log = concat_ws(
                    E'\n',
                    security_review_log,
                    '[MIGRATION] Legacy Python-function skill disabled; re-upload as an Agent Skill package.'
                )
            """
        )

    for column in _OLD_COLUMNS:
        if column in columns:
            op.drop_column("skills", column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("skills")}

    if "code_python" not in columns:
        op.add_column(
            "skills",
            sa.Column("code_python", sa.Text(), nullable=False, server_default=sa.text("''")),
        )
    if "input_schema" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "input_schema",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    if "connector_name" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "connector_name",
                sa.String(length=30),
                nullable=False,
                server_default=sa.text("'internal'"),
            ),
        )
        op.create_check_constraint(
            "ck_skills_connector_name",
            "skills",
            "connector_name IN ('internal', 'ssh', 'clickhouse', 'external_postgres')",
        )
    if "risk_level" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "risk_level",
                sa.String(length=30),
                nullable=False,
                server_default=sa.text("'read_only'"),
            ),
        )
        op.create_check_constraint(
            "ck_skills_risk_level",
            "skills",
            "risk_level IN ('read_only', 'safe_action', 'dangerous_action', 'prohibited')",
        )

    for column in _NEW_COLUMNS:
        if column in columns:
            op.drop_column("skills", column)
