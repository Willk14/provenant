"""Email: work notifications to send, and documents received by email waiting to be filed (ADR 0017).

Revision ID: 0022
Revises: 0021
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TABLES = ("notifications", "inbox_items")


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),  # generic wording and a link: never contract content
        sa.Column("status", sa.String(16), nullable=False),  # queued | sent | failed | skipped
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "inbox_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # new | filed | dismissed
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("handled_by", sa.String(200), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
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
        if role:
            op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {role}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
