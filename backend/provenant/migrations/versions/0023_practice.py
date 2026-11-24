"""Practice agents: matters, parties, conflict checks, KYC items, certificates, screening lists, the client trust
account, and the full AI trace (ADR 0018). History tables are append-only.

Revision ID: 0023
Revises: 0022
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TABLES = ("matters", "matter_parties", "matter_events", "conflict_checks", "matter_requirements",
          "practising_certificates", "watchlist_entries", "trust_accounts", "trust_entries", "trust_reconciliations",
          "ai_traces")
APPEND_ONLY = ("matter_events", "trust_entries", "ai_traces")


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.String(64), nullable=False, index=True)


def _when(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "matters", _id(), _tenant(),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("matter_type", sa.String(40), nullable=False),
        sa.Column("client_kind", sa.String(16), nullable=False),  # person | organisation
        sa.Column("status", sa.String(16), nullable=False),  # conflict_review | open | declined | closed
        sa.Column("responsible", sa.String(200), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        _when("created_at"), _when("updated_at"),
        sa.UniqueConstraint("tenant_id", "reference"),
    )
    op.create_table(
        "matter_parties", _id(), _tenant(),
        sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),  # client | counterparty | related | beneficial_owner
        sa.Column("identifier", sa.String(64), nullable=True),
    )
    op.create_table(
        "matter_events", _id(), _tenant(),
        sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        _when("at"),
    )
    op.create_table(
        "conflict_checks", _id(), _tenant(),
        sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
        sa.Column("hits", sa.Text(), nullable=False),  # JSON, frozen when the check ran
        sa.Column("compared", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # pending | cleared | conflict
        sa.Column("run_by", sa.String(200), nullable=False),
        _when("run_at"),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decision_note", sa.String(1000), nullable=True),
        _when("decided_at", nullable=True),
    )
    op.create_table(
        "matter_requirements", _id(), _tenant(),
        sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
        sa.Column("item", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # provided | verified
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("provided_by", sa.String(200), nullable=False),
        _when("provided_at"),
        sa.Column("verified_by", sa.String(200), nullable=True),
        _when("verified_at", nullable=True),
    )
    op.create_table(
        "practising_certificates", _id(), _tenant(),
        sa.Column("holder", sa.String(320), nullable=False),
        sa.Column("number", sa.String(64), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("recorded_by", sa.String(200), nullable=False),
        _when("recorded_at"),
    )
    op.create_table(
        "watchlist_entries", _id(), _tenant(),
        sa.Column("list_name", sa.String(120), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("identifier", sa.String(64), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("uploaded_by", sa.String(200), nullable=False),
        _when("uploaded_at"),
    )
    op.create_table(
        "trust_accounts", _id(), _tenant(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("bank", sa.String(120), nullable=False),
        sa.Column("dormant_days", sa.Integer(), nullable=True),
        sa.Column("dormant_basis", sa.String(300), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        _when("created_at"),
    )
    op.create_table(
        "trust_entries", _id(), _tenant(),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("trust_accounts.id"), nullable=False, index=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("entered_by", sa.String(200), nullable=False),
        _when("created_at"),
    )
    op.create_table(
        "trust_reconciliations", _id(), _tenant(),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("trust_accounts.id"), nullable=False, index=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("bank_balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("statement_sha256", sa.String(64), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),  # JSON workpaper, frozen when prepared
        sa.Column("balanced", sa.Boolean(), nullable=False),
        sa.Column("prepared_by", sa.String(200), nullable=False),
        _when("prepared_at"),
        sa.Column("approved_by", sa.String(200), nullable=True),
        _when("approved_at", nullable=True),
    )
    op.create_table(
        "ai_traces", _id(), _tenant(),
        sa.Column("run_id", sa.String(36), nullable=False, index=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),  # system | prompt | response | tool_call | tool_result
        sa.Column("content", sa.Text(), nullable=False),
        _when("at"),
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
        if table in APPEND_ONLY:
            op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()""")
        if role:
            grants = "SELECT, INSERT" if table in APPEND_ONLY else "SELECT, INSERT, UPDATE"
            if table == "watchlist_entries":
                grants += ", DELETE"  # a republished list replaces the old one
            op.execute(f"GRANT {grants} ON {table} TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in APPEND_ONLY:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
