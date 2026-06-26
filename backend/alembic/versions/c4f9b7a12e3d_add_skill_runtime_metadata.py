"""add skill runtime metadata

Revision ID: c4f9b7a12e3d
Revises: 86337ce3380d
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "c4f9b7a12e3d"
down_revision: str | None = "86337ce3380d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS "
            "version VARCHAR(30) DEFAULT '1.0.0' NOT NULL"
        )
        op.execute(
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS "
            "connector_name VARCHAR(30) DEFAULT 'internal' NOT NULL"
        )
        op.execute(
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS "
            "risk_level VARCHAR(30) DEFAULT 'read_only' NOT NULL"
        )
        op.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_skills_connector_name'
                ) THEN
                    ALTER TABLE skills ADD CONSTRAINT ck_skills_connector_name
                    CHECK (connector_name IN ('internal', 'ssh', 'clickhouse', 'external_postgres'));
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_skills_risk_level'
                ) THEN
                    ALTER TABLE skills ADD CONSTRAINT ck_skills_risk_level
                    CHECK (risk_level IN ('read_only', 'safe_action', 'dangerous_action', 'prohibited'));
                END IF;
            END $$
            """
        )
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("skills")}

    if "version" not in columns:
        op.add_column(
            "skills",
            sa.Column(
                "version",
                sa.String(length=30),
                nullable=False,
                server_default=sa.text("'1.0.0'"),
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

    inspector = sa.inspect(bind)
    constraint_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("skills")
    }
    if "ck_skills_connector_name" not in constraint_names:
        op.create_check_constraint(
            "ck_skills_connector_name",
            "skills",
            "connector_name IN ('internal', 'ssh', 'clickhouse', 'external_postgres')",
        )
    if "ck_skills_risk_level" not in constraint_names:
        op.create_check_constraint(
            "ck_skills_risk_level",
            "skills",
            "risk_level IN ('read_only', 'safe_action', 'dangerous_action', 'prohibited')",
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("ALTER TABLE skills DROP CONSTRAINT IF EXISTS ck_skills_risk_level")
        op.execute("ALTER TABLE skills DROP CONSTRAINT IF EXISTS ck_skills_connector_name")
        op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS risk_level")
        op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS connector_name")
        op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS version")
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraint_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("skills")
    }
    if "ck_skills_risk_level" in constraint_names:
        op.drop_constraint("ck_skills_risk_level", "skills", type_="check")
    if "ck_skills_connector_name" in constraint_names:
        op.drop_constraint("ck_skills_connector_name", "skills", type_="check")

    columns = {column["name"] for column in sa.inspect(bind).get_columns("skills")}
    if "risk_level" in columns:
        op.drop_column("skills", "risk_level")
    if "connector_name" in columns:
        op.drop_column("skills", "connector_name")
    if "version" in columns:
        op.drop_column("skills", "version")
