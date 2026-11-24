"""Practice agents phase 2: locked precedents and drafts from them, deadline rules, court holidays, court notices,
the docket, and the contracts linked to a matter for due diligence (ADR 0019).

Revision ID: 0024
Revises: 0023
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TABLES = ("precedents", "precedent_versions", "precedent_drafts", "deadline_rules", "court_holidays", "court_notices",
          "docket_entries", "matter_documents")
APPEND_ONLY = ("precedent_versions",)
DELETABLE = ("court_holidays", "matter_documents")


def _base(name: str, *columns: sa.Column, constraints: tuple = ()) -> None:
    op.create_table(name, sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("tenant_id", sa.String(64), nullable=False, index=True), *columns, *constraints)


def _at(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    _base("precedents",
          sa.Column("name", sa.String(120), nullable=False),
          sa.Column("kind", sa.String(32), nullable=False),
          sa.Column("current_version", sa.Integer(), nullable=False),
          sa.Column("created_by", sa.String(200), nullable=False), _at("created_at"))
    _base("precedent_versions",
          sa.Column("precedent_id", sa.String(36), sa.ForeignKey("precedents.id"), nullable=False, index=True),
          sa.Column("version", sa.Integer(), nullable=False),
          sa.Column("body", sa.Text(), nullable=False),
          sa.Column("sha256", sa.String(64), nullable=False),
          sa.Column("locked_by", sa.String(200), nullable=False), _at("locked_at"),
          constraints=(sa.UniqueConstraint("precedent_id", "version"),))
    _base("precedent_drafts",
          sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
          sa.Column("precedent_id", sa.String(36), sa.ForeignKey("precedents.id"), nullable=False),
          sa.Column("version", sa.Integer(), nullable=False),
          sa.Column("field_values", sa.Text(), nullable=False),
          sa.Column("included", sa.Text(), nullable=False),
          sa.Column("standard_text", sa.Text(), nullable=False),
          sa.Column("current_text", sa.Text(), nullable=False),
          sa.Column("status", sa.String(16), nullable=False),  # ready | returned | approved
          sa.Column("created_by", sa.String(200), nullable=False),
          sa.Column("edited_by", sa.String(200), nullable=True),
          sa.Column("decided_by", sa.String(200), nullable=True),
          sa.Column("decision_note", sa.String(1000), nullable=True),
          _at("created_at"), _at("updated_at"))
    _base("deadline_rules",
          sa.Column("name", sa.String(160), nullable=False),
          sa.Column("trigger", sa.String(32), nullable=False),
          sa.Column("days", sa.Integer(), nullable=False),
          sa.Column("months", sa.Integer(), nullable=False),
          sa.Column("counting", sa.String(16), nullable=False),
          sa.Column("roll_forward", sa.Boolean(), nullable=False),
          sa.Column("basis", sa.String(300), nullable=False),
          sa.Column("active", sa.Boolean(), nullable=False),
          sa.Column("created_by", sa.String(200), nullable=False), _at("created_at"))
    _base("court_holidays",
          sa.Column("day", sa.Date(), nullable=False),
          sa.Column("name", sa.String(120), nullable=False),
          constraints=(sa.UniqueConstraint("tenant_id", "day"),))
    _base("court_notices",
          sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
          sa.Column("text", sa.Text(), nullable=False),
          sa.Column("reading", sa.Text(), nullable=False),
          sa.Column("status", sa.String(16), nullable=False),  # new | confirmed | dismissed
          sa.Column("event", sa.String(32), nullable=True),
          sa.Column("event_date", sa.Date(), nullable=True),
          sa.Column("case_number", sa.String(80), nullable=True),
          sa.Column("created_by", sa.String(200), nullable=False),
          sa.Column("confirmed_by", sa.String(200), nullable=True), _at("created_at"))
    _base("docket_entries",
          sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
          sa.Column("notice_id", sa.String(36), sa.ForeignKey("court_notices.id"), nullable=True),
          sa.Column("rule_id", sa.String(36), sa.ForeignKey("deadline_rules.id"), nullable=True),
          sa.Column("title", sa.String(200), nullable=False),
          sa.Column("due_on", sa.Date(), nullable=False, index=True),
          sa.Column("basis", sa.String(400), nullable=False),
          sa.Column("status", sa.String(16), nullable=False),  # open | done
          sa.Column("created_by", sa.String(200), nullable=False),
          sa.Column("done_by", sa.String(200), nullable=True), _at("created_at"), _at("done_at", nullable=True))
    _base("matter_documents",
          sa.Column("matter_id", sa.String(36), sa.ForeignKey("matters.id"), nullable=False, index=True),
          sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
          sa.Column("added_by", sa.String(200), nullable=False), _at("added_at"),
          constraints=(sa.UniqueConstraint("matter_id", "document_id"),))
    if op.get_bind().dialect.name != "postgresql":
        return
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))""")
        if table in APPEND_ONLY:
            op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()""")
        if role:
            grants = "SELECT, INSERT" if table in APPEND_ONLY else "SELECT, INSERT, UPDATE"
            if table in DELETABLE:
                grants += ", DELETE"
            op.execute(f"GRANT {grants} ON {table} TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in APPEND_ONLY:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
