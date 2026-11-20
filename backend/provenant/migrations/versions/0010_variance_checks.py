"""Variance checks: filed returns (VAT, PAYE, WHT certificates) per client-year, and explanations.

Revision ID: 0010
Revises: 0009
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TENANT_TABLES = ("compliance_returns", "variance_explanations")


def _common():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("year_end", sa.Date(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "compliance_returns", *_common(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end", "kind"),
    )
    op.create_table(
        "variance_explanations", *_common(),
        sa.Column("check_key", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("explained_by", sa.String(200), nullable=False),
        sa.Column("explained_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end", "check_key"),
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
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON compliance_returns, variance_explanations TO {role}")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
