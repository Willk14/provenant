"""M-Pesa and bank statements per client-year, and confirmed categories per movement.

Revision ID: 0011
Revises: 0010
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TENANT_TABLES = ("statements", "statement_categories")


def _common():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("year_end", sa.Date(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "statements", *_common(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end", "sha256"),
    )
    op.create_table(
        "statement_categories", *_common(),
        sa.Column("txn_key", sa.String(200), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end", "txn_key"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
        )
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON statements, statement_categories TO {role}")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
