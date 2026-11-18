"""Append-only reviewer snapshots for tax computations.

Revision ID: 0007
Revises: 0006
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tax_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "client_id", "year_end", "fingerprint"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE tax_approvals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tax_approvals FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON tax_approvals
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))""")
    op.execute("""CREATE TRIGGER tax_approvals_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON tax_approvals
        FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()""")
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT ON tax_approvals TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS tax_approvals_append_only ON tax_approvals")
    op.drop_table("tax_approvals")
