"""eTIMS reconciliation: purchase ledgers, eTIMS invoice exports and a person's decisions per purchase.

Revision ID: 0009
Revises: 0008
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TENANT_TABLES = ("purchase_ledgers", "purchase_lines", "etims_exports", "etims_invoices", "etims_decisions")


def _id():
    return sa.Column("id", sa.String(36), primary_key=True)


def _tenant():
    return sa.Column("tenant_id", sa.String(64), nullable=False, index=True)


def _client():
    return sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True)


def _upload_columns():
    return [
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "purchase_ledgers", _id(), _tenant(), _client(), *_upload_columns(),
        sa.Column("applied_by", sa.String(200), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("client_id", "year_end"),
    )
    op.create_table(
        "purchase_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), _tenant(),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("purchase_ledgers.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("supplier", sa.String(200), nullable=False),
        sa.Column("supplier_pin", sa.String(16), nullable=False),
        sa.Column("invoice_number", sa.String(64), nullable=False),
        sa.Column("account_code", sa.String(64), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("etims_number", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
    )
    op.create_table(
        "etims_exports", _id(), _tenant(), _client(), *_upload_columns(),
        sa.UniqueConstraint("client_id", "year_end"),
    )
    op.create_table(
        "etims_invoices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), _tenant(),
        sa.Column("export_id", sa.String(36), sa.ForeignKey("etims_exports.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("cu_invoice_number", sa.String(64), nullable=False),
        sa.Column("supplier_pin", sa.String(16), nullable=False),
        sa.Column("supplier_name", sa.String(200), nullable=False),
        sa.Column("invoice_number", sa.String(64), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("taxable_cents", sa.BigInteger(), nullable=False),
        sa.Column("vat_cents", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "etims_decisions", _id(), _tenant(), _client(),
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("line_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("cu_invoice_number", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end", "line_key"),
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
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON purchase_ledgers, etims_decisions TO {role}")
        op.execute(f"GRANT SELECT, INSERT, DELETE ON purchase_lines, etims_exports, etims_invoices TO {role}")
        op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
