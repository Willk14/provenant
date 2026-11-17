"""Credit agreements and trial balances for covenant testing.

Revision ID: 0004
Revises: 0003
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD_CHECK = (
    "(kind = 'lease' AND parent_id IS NULL AND parent_sha256 IS NULL)"
    " OR (kind = 'amendment' AND parent_id IS NOT NULL AND parent_sha256 IS NOT NULL)"
)
NEW_CHECK = (
    "(kind IN ('lease', 'credit_agreement') AND parent_id IS NULL AND parent_sha256 IS NULL)"
    " OR (kind = 'amendment' AND parent_id IS NOT NULL AND parent_sha256 IS NOT NULL)"
)
NEW_TABLES = ("trial_balances", "trial_balance_lines")


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_documents_amendment_has_parent", type_="check")
        batch.create_check_constraint("ck_documents_amendment_has_parent", NEW_CHECK)

    op.create_table(
        "trial_balances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "period_end"),
    )
    op.create_table(
        "trial_balance_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "trial_balance_id",
            sa.String(36),
            sa.ForeignKey("trial_balances.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in NEW_TABLES:
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
        op.execute(f"GRANT SELECT, INSERT, DELETE ON {', '.join(NEW_TABLES)} TO {role}")
        op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}")


def downgrade() -> None:
    op.drop_table("trial_balance_lines")
    op.drop_table("trial_balances")
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_documents_amendment_has_parent", type_="check")
        batch.create_check_constraint("ck_documents_amendment_has_parent", OLD_CHECK)
