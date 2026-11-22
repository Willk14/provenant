"""Per-test-date covenant facts: revolver drawn (springing tests) and certificate delivery (cure windows).

Revision ID: 0014
Revises: 0013
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "covenant_period_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("revolver_drawn_cents", sa.BigInteger(), nullable=True),
        sa.Column("delivered_on", sa.Date(), nullable=True),
        sa.Column("basis", sa.String(1000), nullable=False),
        sa.Column("entered_by", sa.String(200), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(200), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("document_id", "period_end"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE covenant_period_inputs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE covenant_period_inputs FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY tenant_isolation ON covenant_period_inputs
           USING (tenant_id = current_setting('app.tenant_id', true))
           WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
    )
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON covenant_period_inputs TO {role}")


def downgrade() -> None:
    op.drop_table("covenant_period_inputs")
