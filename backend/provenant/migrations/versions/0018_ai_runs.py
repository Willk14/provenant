"""AI run ledger: what each Claude run used and cost. Append-only, and it holds no document content (ADR 0014).

Revision ID: 0018
Revises: 0017
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),  # a plain id, so the ledger outlives what it describes
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("models", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("error_type", sa.String(64), nullable=True),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("prices_version", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_runs_tenant_created", "ai_runs", ["tenant_id", "created_at"])
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE ai_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_runs FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON ai_runs
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))""")
    op.execute("""CREATE TRIGGER ai_runs_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON ai_runs
        FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()""")
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT ON ai_runs TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS ai_runs_append_only ON ai_runs")
    op.drop_table("ai_runs")
