"""initial_schema

Revision ID: 86337ce3380d
Revises:
Create Date: 2026-06-16 15:44:05.222801
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "86337ce3380d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_sessions_status"),
    )
    op.create_index("idx_sessions_updated_at", "sessions", ["updated_at"])

    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "version", sa.String(length=30), nullable=False, server_default=sa.text("'1.0.0'")
        ),
        sa.Column(
            "connector_name",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'internal'"),
        ),
        sa.Column(
            "risk_level",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'read_only'"),
        ),
        sa.Column(
            "input_schema",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("code_python", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default=sa.text("'uploaded'")
        ),
        sa.Column("security_review_log", sa.Text(), nullable=True),
        sa.Column("is_malicious", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "connector_name IN ('internal', 'ssh', 'clickhouse', 'external_postgres')",
            name="ck_skills_connector_name",
        ),
        sa.CheckConstraint(
            "risk_level IN ('read_only', 'safe_action', 'dangerous_action', 'prohibited')",
            name="ck_skills_risk_level",
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'testing', 'ready', 'rejected')", name="ck_skills_status"
        ),
    )
    op.create_index(
        "idx_skills_status_ready",
        "skills",
        ["status"],
        postgresql_where=sa.text("status = 'ready'"),
    )

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
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'system')", name="ck_audit_logs_actor_type"
        ),
    )
    op.create_index("idx_audit_logs_action_created_at", "audit_logs", ["action", "created_at"])
    op.create_index("idx_audit_logs_run_created_at", "audit_logs", ["run_id", "created_at"])
    op.create_index("idx_audit_logs_session_created_at", "audit_logs", ["session_id", "created_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider", sa.String(length=50), nullable=False, server_default=sa.text("'openai'")
        ),
        sa.Column(
            "model", sa.String(length=100), nullable=False, server_default=sa.text("'gpt-4o'")
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'0.0.1'"),
        ),
        sa.Column(
            "run_config_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("langfuse_trace_id", sa.String(length=100), nullable=True),
        sa.Column("langfuse_trace_url", sa.Text(), nullable=True),
        sa.Column(
            "step_count", sa.Integer(), nullable=False, server_default=sa.text("'0'::integer")
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled', 'timed_out')",
            name="ck_agent_runs_status",
        ),
    )
    op.create_index("idx_agent_runs_session_created_at", "agent_runs", ["session_id", "created_at"])
    op.create_index("idx_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default=sa.text("'completed'")
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_chat_messages_session_sequence"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')", name="ck_chat_messages_role"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed')",
            name="ck_chat_messages_status",
        ),
    )

    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "step_index", name="uq_run_steps_run_step_index"),
        sa.CheckConstraint(
            "step_type IN ('llm_call', 'tool_call', 'approval', 'error')",
            name="ck_run_steps_step_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled', 'timed_out')",
            name="ck_run_steps_status",
        ),
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_steps.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("provider_tool_call_id", sa.String(length=255), nullable=True),
        sa.Column("skill_name", sa.String(length=100), nullable=False),
        sa.Column(
            "skill_version", sa.String(length=30), nullable=False, server_default=sa.text("'1.0.0'")
        ),
        sa.Column(
            "skill_source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'internal'"),
        ),
        sa.Column("connector_name", sa.String(length=100), nullable=True),
        sa.Column(
            "arguments_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "output_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "risk_level",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'read_only'"),
        ),
        sa.Column(
            "requires_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=150), nullable=True, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "skill_source IN ('internal', 'mcp')", name="ck_tool_calls_skill_source"
        ),
        sa.CheckConstraint(
            "risk_level IN ('read_only', 'safe_action', 'dangerous_action', 'prohibited')",
            name="ck_tool_calls_risk_level",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'waiting_approval', 'running', 'completed', 'failed', 'rejected', 'cancelled', 'timed_out')",
            name="ck_tool_calls_status",
        ),
    )
    op.create_index("idx_tool_calls_run_id", "tool_calls", ["run_id"])
    op.create_index("idx_tool_calls_skill", "tool_calls", ["skill_name", "skill_source"])

    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_calls.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')",
            name="ck_approval_requests_status",
        ),
    )
    op.create_index("idx_approval_requests_run_id", "approval_requests", ["run_id"])
    op.create_index(
        "idx_approval_requests_pending",
        "approval_requests",
        ["requested_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_approval_requests_pending", table_name="approval_requests")
    op.drop_index("idx_approval_requests_run_id", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("idx_tool_calls_skill", table_name="tool_calls")
    op.drop_index("idx_tool_calls_run_id", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_table("run_steps")
    op.drop_table("chat_messages")
    op.drop_index("idx_agent_runs_status", table_name="agent_runs")
    op.drop_index("idx_agent_runs_session_created_at", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("idx_audit_logs_session_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_run_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_action_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("idx_skills_status_ready", table_name="skills")
    op.drop_table("skills")
    op.drop_index("idx_sessions_updated_at", table_name="sessions")
    op.drop_table("sessions")
