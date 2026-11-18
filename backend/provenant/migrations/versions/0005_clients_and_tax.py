"""Clients, per-client trial balances, account mappings (with history) and tax inputs.

Revision ID: 0005
Revises: 0004
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TENANT_TABLES = ("clients", "client_trial_balances", "client_tb_lines", "account_mappings",
                 "account_mapping_events", "tax_inputs")


def _id():
    return sa.Column("id", sa.String(36), primary_key=True)


def _tenant():
    return sa.Column("tenant_id", sa.String(64), nullable=False, index=True)


def _client():
    return sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True)


def upgrade() -> None:
    op.create_table(
        "clients", _id(), _tenant(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("client_id", sa.String(36), nullable=True))
        batch.create_index("ix_documents_client_id", ["client_id"])
        batch.create_foreign_key("fk_documents_client_id", "clients", ["client_id"], ["id"])
    op.create_table(
        "client_trial_balances", _id(), _tenant(), _client(),
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end"),
    )
    op.create_table(
        "client_tb_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), _tenant(),
        sa.Column("trial_balance_id", sa.String(36), sa.ForeignKey("client_trial_balances.id"), nullable=False,
                  index=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "account_mappings", _id(), _tenant(), _client(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("fs_line", sa.String(64), nullable=True),
        sa.Column("tax_treatment", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rule", sa.String(255), nullable=True),
        sa.Column("confirmed_by", sa.String(200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "code"),
    )
    op.create_table(
        "account_mapping_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), _tenant(), _client(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("fs_line", sa.String(64), nullable=False),
        sa.Column("tax_treatment", sa.String(32), nullable=False),
        sa.Column("by", sa.String(200), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tax_inputs", _id(), _tenant(), _client(),
        sa.Column("year_end", sa.Date(), nullable=False),
        sa.Column("losses_brought_forward_cents", sa.BigInteger(), nullable=False),
        sa.Column("allowances_json", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "year_end"),
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
    op.execute(
        """CREATE TRIGGER account_mapping_events_append_only
           BEFORE UPDATE OR DELETE OR TRUNCATE ON account_mapping_events
           FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()"""
    )
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON clients, account_mappings, tax_inputs TO {role}")
        op.execute(f"GRANT SELECT, INSERT, DELETE ON client_trial_balances, client_tb_lines TO {role}")
        op.execute(f"GRANT SELECT, INSERT ON account_mapping_events TO {role}")
        op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS account_mapping_events_append_only ON account_mapping_events")
    for table in ("tax_inputs", "account_mapping_events", "account_mappings", "client_tb_lines",
                  "client_trial_balances"):
        op.drop_table(table)
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("fk_documents_client_id", type_="foreignkey")
        batch.drop_index("ix_documents_client_id")
        batch.drop_column("client_id")
    op.drop_table("clients")
