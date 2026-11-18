"""Tax records: credits and prior-year return on tax inputs, fixed asset registers, supporting documents.

Revision ID: 0006
Revises: 0005
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_TABLES = ("fixed_asset_registers", "fixed_assets", "client_attachments")


def _id():
    return sa.Column("id", sa.String(36), primary_key=True)


def _tenant():
    return sa.Column("tenant_id", sa.String(64), nullable=False, index=True)


def _client():
    return sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True)


def upgrade() -> None:
    with op.batch_alter_table("tax_inputs") as batch:
        batch.add_column(sa.Column("credits_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("prior_return_json", sa.Text(), nullable=False, server_default="{}"))
    op.create_table(
        "fixed_asset_registers", _id(), _tenant(), _client(),
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end"),
    )
    op.create_table(
        "fixed_assets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), _tenant(),
        sa.Column("register_id", sa.String(36), sa.ForeignKey("fixed_asset_registers.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("asset_code", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("asset_class", sa.String(100), nullable=False),
        sa.Column("acquired_on", sa.Date(), nullable=False),
        sa.Column("cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("disposed_on", sa.Date(), nullable=True),
        sa.Column("proceeds_cents", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "client_attachments", _id(), _tenant(), _client(),
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("account_code", sa.String(64), nullable=True),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
        op.execute(f"GRANT SELECT, INSERT, DELETE ON fixed_asset_registers, fixed_assets, client_attachments TO {role}")
        op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}")


def downgrade() -> None:
    for table in ("client_attachments", "fixed_assets", "fixed_asset_registers"):
        op.drop_table(table)
    with op.batch_alter_table("tax_inputs") as batch:
        batch.drop_column("prior_return_json")
        batch.drop_column("credits_json")
