"""Paralegal drafts of notices, amendments and letters, with an append-only history (ADR 0015).

Revision ID: 0019
Revises: 0018
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TABLES = ("contract_drafts", "contract_draft_events")


def upgrade() -> None:
    op.create_table(
        "contract_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("open_points", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "contract_draft_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("contract_drafts.id"), nullable=False, index=True),
        sa.Column("action", sa.String(16), nullable=False),  # generated | edited | returned | approved
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
        )
    op.execute("""CREATE TRIGGER contract_draft_events_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON contract_draft_events
        FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()""")
    if role:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON contract_drafts TO {role}")
        op.execute(f"GRANT SELECT, INSERT ON contract_draft_events TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS contract_draft_events_append_only ON contract_draft_events")
    for table in reversed(TABLES):
        op.drop_table(table)
