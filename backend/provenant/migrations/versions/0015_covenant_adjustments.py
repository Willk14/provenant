"""Agreement-defined covenant adjustments: EBITDA add-backs and the leases under frozen GAAP.

Revision ID: 0015
Revises: 0014
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "covenant_adjustments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quarter_end", sa.Date(), nullable=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("capped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lease_document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("clause", sa.String(255), nullable=False),
        sa.Column("basis", sa.String(1000), nullable=False),
        sa.Column("entered_by", sa.String(200), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("document_id", "lease_document_id"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE covenant_adjustments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE covenant_adjustments FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY tenant_isolation ON covenant_adjustments
           USING (tenant_id = current_setting('app.tenant_id', true))
           WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
    )
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON covenant_adjustments TO {role}")


def downgrade() -> None:
    op.drop_table("covenant_adjustments")
